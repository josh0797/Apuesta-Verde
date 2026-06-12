"""Phase F74-post — Football Data Enrichment Normalizer (adapter layer).

Este módulo NO sustituye al schema canónico F74 (`football_data_enrichment.py`)
sino que actúa como **adapter/compatibility layer**: lee TheStatsAPI desde
cualquiera de las ubicaciones donde el pipeline lo persiste hoy y produce un
único payload canónico F74 *extendido* con sub-bloque ``team_stats``
(detalle por equipo). Después lo persiste en **ambas** ubicaciones legacy
(``match["football_data_enrichment"]`` y ``match["thestatsapi_snapshot"]``)
para que los lectores históricos del pipeline encuentren los mismos datos
sin importar qué clave consultaban.

Origen de datos soportado
=========================
  * ``match["_thestatsapi_enrichment"]`` — pre-match TheStatsAPI
  * ``match["thestatsapi_snapshot"]``    — live/snapshot TheStatsAPI
  * ``match["football_data_enrichment"]``— ya normalizado previamente
  * ``match["live_stats"]``              — live stats genéricos

Schema producido (super-set de F74)
===================================
::

    {
      "schema_version": "F74-2",
      "available":      bool,
      "source":         "thestatsapi" | "live_stats" | "merged",
      "data_quality":   "THIN|LIMITED|USABLE|STRONG",
      "providers_used": [...],
      "reason_codes":   [...],
      "xg":             {"home": float|None, "away": float|None},
      "teams":          {"home":{"id","name"}, "away":{"id","name"}},
      "team_stats": {                               # ←  NEW (compat)
        "home": {
          "team":               str|None,
          "xg_for_avg":         float|None,
          "xg_against_avg":     float|None,
          "shots_avg":          float|None,
          "shots_on_target_avg":float|None,
          "possession_avg":     float|None,
          "passes_avg":         float|None,
          "goals_for_avg":      float|None,
          "goals_against_avg":  float|None,
          "yellow_cards_avg":   float|None,
          "red_cards_avg":      float|None,
        },
        "away": { ... mirror ... },
      },
      "official_friendly_split": dict,
      "corners":                 dict,
      "external_context":        {"forebet": dict|None},
      "estimated_probabilities": {},
      "requires_market_identity": bool,
      "market_identity":         dict|None,
    }
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from . import football_data_enrichment as fde

log = logging.getLogger(__name__)

SCHEMA_VERSION = "F74-2"
SOURCE_TAG     = "football_data_enrichment_normalizer"

RC_NORMALIZED        = "FOOTBALL_DATA_ENRICHMENT_NORMALIZED"
RC_MERGED_MULTIPLE   = "ENRICHMENT_MERGED_FROM_MULTIPLE_SOURCES"
RC_NO_TEAM_STATS     = "ENRICHMENT_NO_TEAM_STATS_AVAILABLE"
RC_PERSISTED_LEGACY  = "ENRICHMENT_PERSISTED_LEGACY_KEYS"


# ─────────────────────────────────────────────────────────────────────
# Internal helpers
# ─────────────────────────────────────────────────────────────────────
def _safe_float(v: Any) -> Optional[float]:
    if v is None:
        return None
    try:
        f = float(v)
        if f != f or f in (float("inf"), float("-inf")):
            return None
        return f
    except (TypeError, ValueError):
        return None


# Aliases por métrica (acepta múltiples nombres legacy).
_METRIC_ALIASES: dict[str, tuple[str, ...]] = {
    "xg_for_avg":          ("xg_for_avg", "expected_goals_per_match",
                             "xg_per_match", "xg", "expected_goals_for", "xG"),
    "xg_against_avg":      ("xg_against_avg", "expected_goals_against_per_match",
                             "xga_per_match", "xga", "expected_goals_against", "xGA"),
    "shots_avg":           ("shots_avg", "shots_per_match", "shots_total_avg",
                             "shots"),
    "shots_on_target_avg": ("shots_on_target_avg", "shots_on_target_per_match",
                             "sot_avg", "sot"),
    "possession_avg":      ("possession_avg", "ball_possession_avg",
                             "possession_per_match", "possession"),
    "passes_avg":          ("passes_avg", "passes_per_match", "passes_total"),
    "goals_for_avg":       ("goals_for_avg", "goals_per_match",
                             "goals_scored_per_match", "goals_scored_l5",
                             "goals_for"),
    "goals_against_avg":   ("goals_against_avg", "goals_conceded_per_match",
                             "goals_allowed_l5", "goals_against"),
    "yellow_cards_avg":    ("yellow_cards_avg", "yellow_cards_per_match",
                             "yellow_cards"),
    "red_cards_avg":       ("red_cards_avg", "red_cards_per_match",
                             "red_cards"),
}


def _extract_metric(d: Optional[dict], canonical: str) -> Optional[float]:
    if not isinstance(d, dict):
        return None
    for alias in _METRIC_ALIASES[canonical]:
        v = _safe_float(d.get(alias))
        if v is not None:
            return v
    return None


def _normalize_team_stats_block(side_stats: Optional[dict]) -> dict:
    """Aplana un sub-bloque team_stats[side] a las claves canónicas."""
    if not isinstance(side_stats, dict):
        return {k: None for k in _METRIC_ALIASES} | {"team": None}
    out: dict[str, Any] = {"team": side_stats.get("team") or side_stats.get("name")}
    for canonical in _METRIC_ALIASES:
        out[canonical] = _extract_metric(side_stats, canonical)
    return out


def _extract_corners_block(d: Optional[dict]) -> dict:
    """Best-effort: corners sub-block (si existe)."""
    if not isinstance(d, dict):
        return {}
    corners = d.get("corners") or d.get("corner_stats")
    return corners if isinstance(corners, dict) else {}


def _extract_official_friendly_split(d: Optional[dict]) -> dict:
    if not isinstance(d, dict):
        return {}
    split = d.get("official_friendly_split") or d.get("competition_split")
    return split if isinstance(split, dict) else {}


def _merge_dicts(*sources: Optional[dict]) -> dict:
    """Merge superficial: el primer non-None gana clave por clave."""
    out: dict[str, Any] = {}
    for src in sources:
        if not isinstance(src, dict):
            continue
        for k, v in src.items():
            if k not in out and v is not None:
                out[k] = v
    return out


def _team_stats_root(payload: Optional[dict]) -> Optional[dict]:
    """Devuelve el sub-dict ``team_stats`` (o equivalente) de un payload."""
    if not isinstance(payload, dict):
        return None
    return (
        payload.get("team_stats")
        or payload.get("teams_stats")
        or payload.get("team_statistics")
    )


# ─────────────────────────────────────────────────────────────────────
# Public entry
# ─────────────────────────────────────────────────────────────────────
def normalize_football_data_enrichment(
    match: dict,
    *,
    market_identity: Optional[dict] = None,
    persist: bool = True,
) -> dict:
    """Normaliza datos de TheStatsAPI / live_stats al schema canónico F74-2.

    Lee de las 4 ubicaciones posibles, mergea sin perder señales, y produce
    un payload único listo para consumo del pipeline.

    Si ``persist=True`` (por defecto), reescribe **ambas** claves legacy
    ``match["football_data_enrichment"]`` y ``match["thestatsapi_snapshot"]``
    con el payload normalizado, garantizando que cualquier lector futuro
    encuentre los mismos datos sin importar qué clave use.
    """
    if not isinstance(match, dict):
        match = {}

    ts_pre  = match.get("_thestatsapi_enrichment")
    ts_snap = match.get("thestatsapi_snapshot")
    fde_existing = match.get("football_data_enrichment")
    live_stats   = match.get("live_stats")

    sources_present: list[str] = []
    if isinstance(ts_pre, dict)  and ts_pre:        sources_present.append("_thestatsapi_enrichment")
    if isinstance(ts_snap, dict) and ts_snap:       sources_present.append("thestatsapi_snapshot")
    if isinstance(fde_existing, dict) and fde_existing: sources_present.append("football_data_enrichment")
    if isinstance(live_stats, dict) and live_stats: sources_present.append("live_stats")

    # ── Base F74 canonical via normalize_football_enrichment ─────────
    base = fde.normalize_football_enrichment(
        match, market_identity=market_identity,
    )

    # ── team_stats por equipo: merge desde todas las fuentes ─────────
    home_blocks = []
    away_blocks = []
    for src in (ts_pre, ts_snap, fde_existing, live_stats):
        ts_root = _team_stats_root(src)
        if isinstance(ts_root, dict):
            if isinstance(ts_root.get("home"), dict):
                home_blocks.append(ts_root["home"])
            if isinstance(ts_root.get("away"), dict):
                away_blocks.append(ts_root["away"])

    merged_home = _merge_dicts(*home_blocks)
    merged_away = _merge_dicts(*away_blocks)

    team_stats = {
        "home": _normalize_team_stats_block(merged_home),
        "away": _normalize_team_stats_block(merged_away),
    }

    # Si no había xG en base["xg"], intentar derivarlo desde team_stats.
    if base["xg"]["home"] is None and team_stats["home"]["xg_for_avg"] is not None:
        base["xg"]["home"] = team_stats["home"]["xg_for_avg"]
    if base["xg"]["away"] is None and team_stats["away"]["xg_for_avg"] is not None:
        base["xg"]["away"] = team_stats["away"]["xg_for_avg"]

    # Corners + competition split: best-effort merge.
    corners_block = {}
    of_split = {}
    for src in (ts_pre, ts_snap, fde_existing, live_stats):
        if not isinstance(src, dict):
            continue
        if not corners_block:
            corners_block = _extract_corners_block(src)
        if not of_split:
            of_split = _extract_official_friendly_split(src)

    # ── Compose final payload (F74-2 extended) ───────────────────────
    payload: dict[str, Any] = dict(base)
    payload["schema_version"] = SCHEMA_VERSION
    payload["available"]      = (payload.get("data_quality") != fde.DQ_THIN)
    payload["source"]         = (
        "merged" if len(sources_present) >= 2
        else (sources_present[0] if sources_present else "none")
    )
    payload["team_stats"]              = team_stats
    payload["corners"]                 = corners_block
    payload["official_friendly_split"] = of_split

    codes = list(payload.get("reason_codes") or [])
    if RC_NORMALIZED not in codes:
        codes.append(RC_NORMALIZED)
    if len(sources_present) >= 2 and RC_MERGED_MULTIPLE not in codes:
        codes.append(RC_MERGED_MULTIPLE)
    if (not home_blocks and not away_blocks
            and RC_NO_TEAM_STATS not in codes):
        codes.append(RC_NO_TEAM_STATS)
    payload["reason_codes"] = codes

    # ── Persistencia legacy ──────────────────────────────────────────
    if persist:
        match["football_data_enrichment"] = payload
        # Alias para back-compat (capas que ya leían thestatsapi_snapshot).
        match["thestatsapi_snapshot"]     = payload
        if RC_PERSISTED_LEGACY not in payload["reason_codes"]:
            payload["reason_codes"].append(RC_PERSISTED_LEGACY)

    return payload


__all__ = [
    "SCHEMA_VERSION",
    "RC_NORMALIZED", "RC_MERGED_MULTIPLE",
    "RC_NO_TEAM_STATS", "RC_PERSISTED_LEGACY",
    "normalize_football_data_enrichment",
]
