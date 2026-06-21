"""Sprint-D9 · Cache xG OFFLINE — historical seed.

Carga el dataset histórico ``all_leagues_enriched_dataset.json`` (4338
partidos EPL/LaLiga/SerieA/Bundesliga 2021-2023 con xG por equipo
proveniente de Understat) en MongoDB para usarlo como **fallback
permanente** cuando los sources online (Understat/FBref/footystats/
TheStatsAPI) están rate-limited o caídos.

Diferencia vs. ``football_team_xg_history``:
  * ``football_team_xg_history`` → cache TTL (7 días) de fetches online.
  * ``football_team_xg_offline_seed`` → datos permanentes del dataset
    histórico. NO tiene TTL. Es la "memoria fría" del sistema.

Colección
---------
``football_team_xg_offline_seed``::

    {
      "team_norm":     str  (lowercased + stripped),
      "team_name":     str  (canónico tal como viene del dataset),
      "league":        str  (EPL | LaLiga | SerieA | Bundesliga | …),
      "matches":       [
        {"date": "YYYY-MM-DD", "opponent": str,
          "xg_for": float, "xg_against": float,
          "goals_for": int, "goals_against": int,
          "venue": "home" | "away", "season": str},
        …
      ],
      "matches_count": int,
      "seeded_at":     datetime utc,
      "source":        "historical_dataset_2021_2023",
    }

Public API
----------
:func:`build_seed_from_dataset`
    Lee el JSON crudo y devuelve la lista de docs por equipo.
:func:`persist_seed`
    Upsert por (team_norm, league) en la colección.
:func:`get_offline_xg_history`
    Lookup async: devuelve la lista ``matches`` para
    ``(team_name[, league])``.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

log = logging.getLogger("services.football_xg_offline_seed")

OFFLINE_COLLECTION = "football_team_xg_offline_seed"
DEFAULT_DATASET_PATH = Path("/app/data/corners_history/all_leagues_enriched_dataset.json")


def _norm_team(name: str) -> str:
    if not isinstance(name, str):
        return ""
    import unicodedata
    # Lowercase + strip + remove diacritics (accents). The dataset uses
    # bare ASCII ("atletico", "monchengladbach"), so normalising NFKD +
    # filtering combining chars converges to the same key regardless of
    # how the source spelled the team ("Atlético Madrid" → "atletico madrid").
    s = unicodedata.normalize("NFKD", name.strip().lower())
    return "".join(c for c in s if not unicodedata.combining(c))


# Alias canónicos para variantes de naming entre proveedores.
# Mapeo: alias-cualquiera-en-input → alias-tal-como-está-en-el-dataset.
# El lookup intenta primero el match exacto, luego el alias.
_TEAM_ALIASES: dict[str, str] = {
    # EPL
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
    # LaLiga (Football-Data.co.uk usa abreviaciones: "Ath Madrid", "Sociedad", …)
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
    # SerieA
    "internazionale":        "inter",
    "internazionale milano": "inter",
    "ac milan":              "milan",
    "as roma":               "roma",
    "ssc napoli":            "napoli",
    # Bundesliga
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

    # ── Selecciones nacionales (Sprint-D9 Fix 1a) ──────────────────
    # Mapeo ES↔EN + variantes oficiales↔abreviadas. La clave es siempre
    # el ASCII lowercase del nombre canónico-corto que usa Understat /
    # FBref (sin "team", "national team", "selección de", etc.).
    # España (Spain)
    "espana":                "spain",
    "españa":                "spain",
    "seleccion de espana":   "spain",
    "selección de españa":   "spain",
    "spain national team":   "spain",
    # Países Bajos / Holanda
    "paises bajos":          "netherlands",
    "países bajos":          "netherlands",
    "holanda":               "netherlands",
    "holland":               "netherlands",
    # Inglaterra / England
    "inglaterra":            "england",
    # Francia / France
    "francia":               "france",
    # Alemania / Germany
    "alemania":              "germany",
    "deutschland":           "germany",
    # Italia / Italy
    "italia":                "italy",
    # Bélgica / Belgium
    "belgica":               "belgium",
    "bélgica":               "belgium",
    # Croacia / Croatia
    "croacia":               "croatia",
    "hrvatska":              "croatia",
    # Portugal
    "portugal":              "portugal",
    # Suiza / Switzerland
    "suiza":                 "switzerland",
    # Polonia / Poland
    "polonia":               "poland",
    "polska":                "poland",
    # Dinamarca / Denmark
    "dinamarca":             "denmark",
    # Suecia / Sweden
    "suecia":                "sweden",
    "sverige":               "sweden",
    # Noruega / Norway
    "noruega":               "norway",
    "norge":                 "norway",
    # Finlandia / Finland
    "finlandia":             "finland",
    "suomi":                 "finland",
    # República Checa / Czech Republic
    "republica checa":       "czech republic",
    "república checa":       "czech republic",
    "czechia":               "czech republic",
    # Turquía / Turkey
    "turquia":               "turkey",
    "turquía":               "turkey",
    "türkiye":               "turkey",
    "turkiye":               "turkey",
    # Ucrania / Ukraine
    "ucrania":               "ukraine",
    # Austria
    "austria":               "austria",
    # Hungría / Hungary
    "hungria":               "hungary",
    "hungría":               "hungary",
    "magyarország":          "hungary",
    "magyarorszag":          "hungary",
    # Rumania / Romania
    "rumania":               "romania",
    "rumanía":               "romania",
    "románia":               "romania",
    # Escocia / Scotland
    "escocia":               "scotland",
    # Albania
    "albania":               "albania",
    "shqipëria":             "albania",
    "shqiperia":             "albania",
    # Grecia / Greece
    "grecia":                "greece",
    # Serbia / Srbija
    "serbia":                "serbia",
    "srbija":                "serbia",
    # Sudamérica
    # Argentina
    "argentina":             "argentina",
    "seleccion argentina":   "argentina",
    "selección argentina":   "argentina",
    # Brasil / Brazil
    "brasil":                "brazil",
    # Uruguay
    "uruguay":               "uruguay",
    # Colombia
    "colombia":              "colombia",
    # Chile
    "chile":                 "chile",
    # Perú / Peru
    "peru":                  "peru",
    "perú":                  "peru",
    # Ecuador
    "ecuador":               "ecuador",
    # Paraguay
    "paraguay":              "paraguay",
    # Bolivia
    "bolivia":               "bolivia",
    # Venezuela
    "venezuela":             "venezuela",
    # CONCACAF / Centro-Norteamérica
    # México / Mexico
    "mexico":                "mexico",
    "méxico":                "mexico",
    # Estados Unidos / USA
    "estados unidos":        "usa",
    "united states":         "usa",
    "us":                    "usa",
    "usmnt":                 "usa",
    # Canadá / Canada
    "canada":                "canada",
    "canadá":                "canada",
    # Costa Rica
    "costa rica":            "costa rica",
    # Panamá / Panama
    "panama":                "panama",
    "panamá":                "panama",
    # Honduras
    "honduras":              "honduras",
    # Curaçao (caso de la captura del usuario)
    "curacao":               "curacao",
    "curaçao":               "curacao",
    # Jamaica
    "jamaica":               "jamaica",
    # África
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
    # Asia
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
    # Cabo Verde (caso de la captura: Uruguay vs Cape Verde)
    "cabo verde":            "cape verde",
}


def _canonicalize_team(name: str) -> str:
    """Devuelve el team_norm canónico tras pasar por el mapa de aliases."""
    raw = _norm_team(name)
    if not raw:
        return ""
    return _TEAM_ALIASES.get(raw, raw)


def build_seed_from_dataset(
    dataset_path: Path = DEFAULT_DATASET_PATH,
) -> list[dict]:
    """Lee el dataset histórico y agrupa por (team_norm, league).

    Cada equipo acumula AMBOS lados: cuando es local (xg_h, xg_a)
    se registra como ``home`` con ``xg_for=xg_h, xg_against=xg_a``,
    y cuando es visitante como ``away``. El opponent es el otro equipo.
    """
    p = Path(dataset_path)
    if not p.exists():
        log.warning("[xg_offline_seed] dataset not found: %s", p)
        return []

    raw = json.loads(p.read_text())
    log.info("[xg_offline_seed] loaded %d matches from %s", len(raw), p.name)

    by_team: dict[tuple[str, str], dict] = {}

    def _ensure(team_name: str, league: str) -> dict:
        key = (_norm_team(team_name), league)
        if key not in by_team:
            by_team[key] = {
                "team_norm": key[0],
                "team_name": team_name,
                "league":    league,
                "matches":   [],
            }
        return by_team[key]

    for row in raw:
        league = row.get("league") or ""
        season = row.get("season") or ""
        date   = row.get("date")
        home   = row.get("home_team")
        away   = row.get("away_team")
        xg_h   = row.get("xg_h")
        xg_a   = row.get("xg_a")
        gh     = row.get("home_corners")  # placeholder; goals NO están en dataset corners
        ga     = row.get("away_corners")
        # Si el dataset tiene los goles explícitos, usarlos
        gh = row.get("home_goals", gh)
        ga = row.get("away_goals", ga)

        # Validación mínima
        if not home or not away or xg_h is None or xg_a is None:
            continue

        h_doc = _ensure(home, league)
        h_doc["matches"].append({
            "date":         date,
            "opponent":     away,
            "xg_for":       float(xg_h),
            "xg_against":   float(xg_a),
            "goals_for":    int(gh) if gh is not None else None,
            "goals_against": int(ga) if ga is not None else None,
            "venue":        "home",
            "season":       season,
        })
        a_doc = _ensure(away, league)
        a_doc["matches"].append({
            "date":         date,
            "opponent":     home,
            "xg_for":       float(xg_a),
            "xg_against":   float(xg_h),
            "goals_for":    int(ga) if ga is not None else None,
            "goals_against": int(gh) if gh is not None else None,
            "venue":        "away",
            "season":       season,
        })

    # Sort cada equipo por fecha asc (importante para windowing L5/L15)
    docs: list[dict] = []
    for doc in by_team.values():
        doc["matches"].sort(key=lambda m: m.get("date") or "")
        doc["matches_count"] = len(doc["matches"])
        doc["seeded_at"]     = datetime.now(timezone.utc)
        doc["source"]        = "historical_dataset_2021_2023"
        docs.append(doc)

    log.info(
        "[xg_offline_seed] built %d (team, league) docs from %d matches",
        len(docs), len(raw),
    )
    return docs


async def ensure_offline_indexes(db: Any) -> dict:
    """Crea índices necesarios. Idempotente."""
    if db is None:
        return {"created": [], "skipped": "no_db"}
    try:
        coll = db[OFFLINE_COLLECTION]
    except Exception:
        return {"created": [], "skipped": "no_collection"}
    created: list[str] = []
    try:
        await coll.create_index(
            [("team_norm", 1), ("league", 1)],
            unique=True, name="ix_team_league_offline",
        )
        created.append("ix_team_league_offline")
    except Exception as exc:
        log.warning("[xg_offline_seed] ix_team_league_offline failed: %s", exc)
    return {"created": created}


async def persist_seed(db: Any, docs: list[dict]) -> dict:
    """Upsert por (team_norm, league). Devuelve el conteo de inserts/updates."""
    if db is None or not docs:
        return {"upserted": 0, "modified": 0}
    coll = db[OFFLINE_COLLECTION]
    upserted = 0
    modified = 0
    for d in docs:
        try:
            res = await coll.update_one(
                {"team_norm": d["team_norm"], "league": d["league"]},
                {"$set": d},
                upsert=True,
            )
            if res.upserted_id:
                upserted += 1
            elif res.modified_count > 0:
                modified += 1
        except Exception as exc:
            log.warning("[xg_offline_seed] upsert failed for %s: %s",
                         d.get("team_name"), exc)
    log.info("[xg_offline_seed] persisted: upserted=%d, modified=%d",
              upserted, modified)
    return {"upserted": upserted, "modified": modified, "total": len(docs)}


async def get_offline_xg_history(
    db: Any,
    team_name: str,
    *,
    league: Optional[str] = None,
    limit_matches: Optional[int] = None,
) -> Optional[dict]:
    """Lookup async de matches xG offline para un equipo.

    Si ``league`` es ``None``, busca en cualquier liga (toma la primera
    coincidencia con más matches). Si se pasa ``league``, filtra por
    coincidencia exacta.

    Sprint-D9 Fix 2: además del lookup por team_norm canónico, si no se
    encuentra y ``league`` no se especifica, intenta el match por el
    team_name CRUDO (sin pasar por aliases) como último recurso, por si
    el dataset tiene el equipo con un nombre no aliased.
    """
    if db is None or not team_name:
        return None
    coll = db[OFFLINE_COLLECTION]
    canon = _canonicalize_team(team_name)
    q: dict = {"team_norm": canon}
    if league:
        q["league"] = league
    try:
        if league:
            doc = await coll.find_one(q)
        else:
            cursor = coll.find(q).sort("matches_count", -1).limit(1)
            doc = await cursor.to_list(length=1)
            doc = doc[0] if doc else None
    except Exception as exc:
        log.warning("[xg_offline_seed] lookup failed: %s", exc)
        return None
    if doc is None:
        # Fallback: si el canónico no matcheó, intentamos con el raw norm
        raw = _norm_team(team_name)
        if raw and raw != canon:
            try:
                q2 = {"team_norm": raw}
                if league:
                    q2["league"] = league
                doc = await coll.find_one(q2)
            except Exception:  # noqa: BLE001
                doc = None
    if doc is None:
        return None
    matches = doc.get("matches") or []
    # Devolver los últimos N (matches ya está sorted asc)
    if limit_matches:
        matches = matches[-int(limit_matches):]
    return {
        "available":     True,
        "source":        "offline_seed",
        "team_name":     doc.get("team_name"),
        "league":        doc.get("league"),
        "matches":       matches,
        "matches_count": len(matches),
        "from_cache":    True,
        "reason_code":   "XG_OFFLINE_SEED_HIT",
        "seeded_at":     (doc.get("seeded_at").isoformat()
                            if isinstance(doc.get("seeded_at"), datetime)
                            else doc.get("seeded_at")),
    }


__all__ = [
    "OFFLINE_COLLECTION",
    "DEFAULT_DATASET_PATH",
    "build_seed_from_dataset",
    "ensure_offline_indexes",
    "persist_seed",
    "get_offline_xg_history",
    "promote_online_matches_to_seed",
    "merge_matches_dedupe",
]


# ============================================================
# Sprint-D9 Fix 2b — Promoción online → seed (merge inteligente)
# ============================================================

def _match_key(m: dict) -> tuple:
    """Genera una key estable para dedupe: (date, opponent_normalized)."""
    date = (m.get("date") or "").strip()
    opp_raw = m.get("opponent") or ""
    opp_norm = _norm_team(opp_raw)
    return (date, opp_norm)


def merge_matches_dedupe(
    existing: list[dict],
    incoming: list[dict],
) -> list[dict]:
    """UNION-merge de dos listas de matches con dedupe por (date, opponent).

    Estrategia:
      * Indexar ``existing`` por ``_match_key``.
      * Para cada match en ``incoming``: si la key ya existe, conservar
        el que tenga xG numérico (preferir incoming si ambos lo tienen
        — son más frescos). Si la key NO existe, append.
      * Sort final por date asc.

    Esto es seguro: nunca pierde matches del existing; solo añade o
    actualiza con datos más recientes.
    """
    if not incoming:
        return list(existing or [])
    if not existing:
        return list(incoming)

    by_key: dict[tuple, dict] = {}
    for m in existing:
        by_key[_match_key(m)] = m
    for m in incoming:
        k = _match_key(m)
        prev = by_key.get(k)
        if prev is None:
            by_key[k] = m
            continue
        # Mantener prev a menos que incoming traiga xG que prev no tiene.
        prev_has_xg = (prev.get("xg_for") is not None
                        or prev.get("xg_against") is not None)
        new_has_xg = (m.get("xg_for") is not None
                       or m.get("xg_against") is not None)
        if new_has_xg and not prev_has_xg:
            by_key[k] = m
        elif new_has_xg and prev_has_xg:
            # Incoming es más reciente — preferir
            by_key[k] = m

    merged = sorted(by_key.values(), key=lambda r: r.get("date") or "")
    return merged


async def promote_online_matches_to_seed(
    db: Any,
    *,
    team_name: str,
    league: Optional[str],
    matches: list[dict],
    underlying_source: Optional[str] = None,
) -> dict:
    """Promueve matches fetched online al offline_seed con merge inteligente.

    Si el (team_norm, league) ya existe en el seed, hace UNION+dedupe
    con la lista actual y persiste el resultado SOLO si:
      * el merged tiene MÁS matches que el existente, O
      * el merged tiene la misma cantidad pero al menos un match con
        ``xg_for`` no nulo que antes era nulo.

    Devuelve ``{action, before_count, after_count, league, team_norm}``.
    Nunca raises; en caso de fallo retorna ``{action: "skipped",
    reason: str}``.
    """
    if db is None or not team_name or not matches:
        return {"action": "skipped", "reason": "missing_inputs"}
    try:
        coll = db[OFFLINE_COLLECTION]
    except Exception as exc:  # noqa: BLE001
        return {"action": "skipped", "reason": f"no_collection: {exc}"}

    team_norm = _canonicalize_team(team_name)
    lg = league or "Unknown"

    try:
        existing_doc = await coll.find_one({"team_norm": team_norm, "league": lg})
    except Exception as exc:  # noqa: BLE001
        log.warning("[xg_offline_seed.promote] find_one failed: %s", exc)
        existing_doc = None

    existing_matches: list[dict] = (existing_doc or {}).get("matches") or []
    before_count = len(existing_matches)
    merged = merge_matches_dedupe(existing_matches, matches)
    after_count = len(merged)

    # Solo persistir si hay mejora: más matches o mejor xG coverage.
    new_with_xg = sum(1 for m in merged if m.get("xg_for") is not None)
    prev_with_xg = sum(1 for m in existing_matches if m.get("xg_for") is not None)
    has_improvement = (after_count > before_count) or (new_with_xg > prev_with_xg)
    if not has_improvement:
        return {
            "action":       "no_change",
            "before_count": before_count,
            "after_count":  after_count,
            "team_norm":    team_norm,
            "league":       lg,
        }

    doc = {
        "team_norm":     team_norm,
        "team_name":     team_name,
        "league":        lg,
        "matches":       merged,
        "matches_count": after_count,
        "seeded_at":     datetime.now(timezone.utc),
        "source":        "promoted_from_online",
        "underlying_source": underlying_source or "unknown",
    }
    try:
        await coll.update_one(
            {"team_norm": team_norm, "league": lg},
            {"$set": doc},
            upsert=True,
        )
        log.info(
            "[xg_offline_seed.promote] %s|%s: %d → %d matches (source=%s)",
            team_norm, lg, before_count, after_count, underlying_source,
        )
        return {
            "action":       "promoted",
            "before_count": before_count,
            "after_count":  after_count,
            "team_norm":    team_norm,
            "league":       lg,
            "delta":        after_count - before_count,
        }
    except Exception as exc:  # noqa: BLE001
        log.warning("[xg_offline_seed.promote] upsert failed: %s", exc)
        return {"action": "skipped", "reason": f"upsert_failed: {exc}"}
