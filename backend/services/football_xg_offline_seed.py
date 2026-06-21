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
]
