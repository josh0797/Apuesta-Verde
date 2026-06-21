"""Sprint-D9 Iteration-3 · Cache OFFLINE de corners por equipo.

Hermano de ``football_xg_offline_seed`` — comparte filosofía, contrato y
módulo de aliases (``services.team_aliases``).

Origen del seed
---------------
Bootstrap inicial desde ``all_leagues_enriched_dataset.json`` (4338
partidos EPL/LaLiga/SerieA/Bundesliga 2021-2023) que ya trae
``home_corners`` / ``away_corners``. Cobertura: ~95 clubs top-tier.

Las selecciones nacionales NO están en el dataset histórico inicial —
se llenan orgánicamente vía el sistema de **promote online → seed**
cuando ``fetch_team_corners_history_v2`` obtiene datos frescos de
TheStatsAPI / API-Sports.

Colección
---------
``football_team_corners_offline_seed``::

    {
      "team_norm":     str  (canónico, NFKD-stripped),
      "team_name":     str  (display name original),
      "league":        str  (EPL | LaLiga | SerieA | Bundesliga | National Teams | …),
      "matches":       [
        {"date": "YYYY-MM-DD", "opponent": str,
          "corners_for": int, "corners_against": int,
          "goals_for": int, "goals_against": int,
          "venue": "home" | "away", "season": str},
        …
      ],
      "matches_count": int,
      "seeded_at":     datetime utc,
      "source":        "historical_dataset_2021_2023" | "promoted_from_online",
      "underlying_source": Optional[str]  (cuando source=promoted_from_online),
    }

Diferencia vs. la cache TTL legacy ``team_corners_history``
-----------------------------------------------------------
* ``team_corners_history``      → cache TTL (24h) keyed por team_id+source.
* ``football_team_corners_offline_seed`` → **fría permanente**, sin TTL,
  keyed por (team_norm canónico, league). Sobrevive entre reinicios y
  rate-limits de Scrape.do.

Public API
----------
:func:`build_seed_from_dataset` · :func:`ensure_offline_indexes`
:func:`persist_seed` · :func:`get_offline_corners_history`
:func:`promote_online_matches_to_seed` · :func:`merge_matches_dedupe`
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from .team_aliases import (
    canonicalize_team as _canonicalize_team,
    normalize_team_name as _norm_team,
)

log = logging.getLogger("services.football_corners_offline_seed")

OFFLINE_COLLECTION = "football_team_corners_offline_seed"
DEFAULT_DATASET_PATH = Path(
    "/app/data/corners_history/all_leagues_enriched_dataset.json"
)


# ============================================================
# Bootstrap desde dataset histórico
# ============================================================

def build_seed_from_dataset(
    dataset_path: Path = DEFAULT_DATASET_PATH,
) -> list[dict]:
    """Lee el dataset histórico y agrupa por (team_norm, league).

    Por cada partido genera DOS entries (una por equipo, AMBOS lados):
    home_team obtiene ``corners_for=home_corners, corners_against=away_corners``;
    away_team al revés.
    """
    p = Path(dataset_path)
    if not p.exists():
        log.warning("[corners_offline_seed] dataset not found: %s", p)
        return []

    raw = json.loads(p.read_text())
    log.info(
        "[corners_offline_seed] loaded %d matches from %s", len(raw), p.name,
    )

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
        hc     = row.get("home_corners")
        ac     = row.get("away_corners")
        # Goles: si el dataset los incluye úsalos; si no, None.
        gh = row.get("home_goals")
        ga = row.get("away_goals")

        if not home or not away or hc is None or ac is None:
            continue

        h_doc = _ensure(home, league)
        h_doc["matches"].append({
            "date":          date,
            "opponent":      away,
            "corners_for":   int(hc),
            "corners_against": int(ac),
            "goals_for":     int(gh) if gh is not None else None,
            "goals_against": int(ga) if ga is not None else None,
            "venue":         "home",
            "season":        season,
        })
        a_doc = _ensure(away, league)
        a_doc["matches"].append({
            "date":          date,
            "opponent":      home,
            "corners_for":   int(ac),
            "corners_against": int(hc),
            "goals_for":     int(ga) if ga is not None else None,
            "goals_against": int(gh) if gh is not None else None,
            "venue":         "away",
            "season":        season,
        })

    docs: list[dict] = []
    for doc in by_team.values():
        doc["matches"].sort(key=lambda m: m.get("date") or "")
        doc["matches_count"] = len(doc["matches"])
        doc["seeded_at"]     = datetime.now(timezone.utc)
        doc["source"]        = "historical_dataset_2021_2023"
        docs.append(doc)

    log.info(
        "[corners_offline_seed] built %d (team, league) docs from %d matches",
        len(docs), len(raw),
    )
    return docs


# ============================================================
# Persistence
# ============================================================

async def ensure_offline_indexes(db: Any) -> dict:
    """Crea índices (team_norm, league) único. Idempotente."""
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
            unique=True, name="ix_corners_team_league_offline",
        )
        created.append("ix_corners_team_league_offline")
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "[corners_offline_seed] ix_corners_team_league_offline failed: %s",
            exc,
        )
    return {"created": created}


async def persist_seed(db: Any, docs: list[dict]) -> dict:
    """Upsert idempotente por (team_norm, league)."""
    if db is None or not docs:
        return {"upserted": 0, "modified": 0, "total": 0}
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
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "[corners_offline_seed] upsert failed for %s: %s",
                d.get("team_name"), exc,
            )
    log.info(
        "[corners_offline_seed] persisted: upserted=%d, modified=%d",
        upserted, modified,
    )
    return {"upserted": upserted, "modified": modified, "total": len(docs)}


# ============================================================
# Lookup
# ============================================================

async def get_offline_corners_history(
    db: Any,
    team_name: str,
    *,
    league: Optional[str] = None,
    limit_matches: Optional[int] = None,
) -> Optional[dict]:
    """Lookup async de matches corners offline para un equipo.

    Si ``league`` es ``None``, busca en cualquier liga y devuelve la
    coincidencia con MÁS matches (mejor cobertura).
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
    except Exception as exc:  # noqa: BLE001
        log.warning("[corners_offline_seed] lookup failed: %s", exc)
        return None
    # Fallback con raw norm si canon no matcheó
    if doc is None:
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
        "reason_code":   "CORNERS_OFFLINE_SEED_HIT",
        "seeded_at":     (doc.get("seeded_at").isoformat()
                            if isinstance(doc.get("seeded_at"), datetime)
                            else doc.get("seeded_at")),
    }


# ============================================================
# Promote online → seed (merge inteligente)
# ============================================================

def _match_key(m: dict) -> tuple:
    """Key estable para dedupe: (date, opponent_normalized)."""
    return ((m.get("date") or "").strip(), _norm_team(m.get("opponent") or ""))


def merge_matches_dedupe(
    existing: list[dict],
    incoming: list[dict],
) -> list[dict]:
    """UNION-merge con dedupe por (date, opponent_normalized).

    * Si la key ya existe → mantener el match con ``corners_for`` no nulo;
      preferir incoming cuando ambos tienen datos (es más reciente).
    * Si la key NO existe → append.
    * Sort final por date asc.

    Nunca pierde matches del existing.
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
        prev_has_corners = (prev.get("corners_for") is not None
                             or prev.get("corners_against") is not None)
        new_has_corners = (m.get("corners_for") is not None
                            or m.get("corners_against") is not None)
        if new_has_corners and not prev_has_corners:
            by_key[k] = m
        elif new_has_corners and prev_has_corners:
            by_key[k] = m   # incoming es más fresco

    return sorted(by_key.values(), key=lambda r: r.get("date") or "")


async def promote_online_matches_to_seed(
    db: Any,
    *,
    team_name: str,
    league: Optional[str],
    matches: list[dict],
    underlying_source: Optional[str] = None,
) -> dict:
    """Promueve matches fetched online al seed permanente con merge inteligente.

    Garantías:
      * Idempotente: re-llamar con la misma data → ``no_change``.
      * Nunca pierde matches existentes.
      * Solo persiste si hay mejora real (más matches O más corners coverage).
      * Acepta matches con shape ``{date, opponent, corners_for, corners_against,
        venue, goals_for?, goals_against?, season?}``.
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
        existing_doc = await coll.find_one(
            {"team_norm": team_norm, "league": lg},
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("[corners_offline_seed.promote] find_one failed: %s", exc)
        existing_doc = None

    existing_matches: list[dict] = (existing_doc or {}).get("matches") or []
    before_count = len(existing_matches)
    merged = merge_matches_dedupe(existing_matches, matches)
    after_count = len(merged)

    new_with_corners = sum(1 for m in merged
                            if m.get("corners_for") is not None)
    prev_with_corners = sum(1 for m in existing_matches
                             if m.get("corners_for") is not None)
    has_improvement = (after_count > before_count) or (
        new_with_corners > prev_with_corners
    )
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
            "[corners_offline_seed.promote] %s|%s: %d → %d (source=%s)",
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
        log.warning(
            "[corners_offline_seed.promote] upsert failed: %s", exc,
        )
        return {"action": "skipped", "reason": f"upsert_failed: {exc}"}


# ============================================================
# Window helpers (L5 / L15 / L20 stats)
# ============================================================

def compute_window_stats(
    matches: list[dict],
    *,
    window: int = 15,
) -> dict:
    """Calcula estadísticas L{window} a partir de la lista de matches.

    Devuelve::

        {
          "window":          int,
          "sample_size":     int,  # matches efectivos (≤ window)
          "corners_for_avg": float | None,
          "corners_against_avg": float | None,
          "corners_for_recent": list[int],  # los últimos N raw
          "corners_against_recent": list[int],
        }
    """
    if not matches:
        return {
            "window":               window,
            "sample_size":          0,
            "corners_for_avg":      None,
            "corners_against_avg": None,
            "corners_for_recent":   [],
            "corners_against_recent": [],
        }
    sliced = matches[-int(window):] if window > 0 else matches
    f_vals = [m.get("corners_for") for m in sliced if m.get("corners_for") is not None]
    a_vals = [m.get("corners_against") for m in sliced if m.get("corners_against") is not None]
    return {
        "window":               window,
        "sample_size":          len(sliced),
        "corners_for_avg":      round(sum(f_vals) / len(f_vals), 2) if f_vals else None,
        "corners_against_avg":  round(sum(a_vals) / len(a_vals), 2) if a_vals else None,
        "corners_for_recent":   [int(m["corners_for"]) for m in sliced
                                  if m.get("corners_for") is not None],
        "corners_against_recent": [int(m["corners_against"]) for m in sliced
                                    if m.get("corners_against") is not None],
    }


__all__ = [
    "OFFLINE_COLLECTION",
    "DEFAULT_DATASET_PATH",
    "build_seed_from_dataset",
    "ensure_offline_indexes",
    "persist_seed",
    "get_offline_corners_history",
    "promote_online_matches_to_seed",
    "merge_matches_dedupe",
    "compute_window_stats",
]
