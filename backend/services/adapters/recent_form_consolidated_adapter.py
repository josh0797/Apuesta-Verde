"""Sprint-F99.4 · Recent-form consolidated → F74 envelope adapter (puro).

Convierte la salida de ``football_recent_form_consolidator.consolidate_recent_form``
en envelopes F98/F74 que el cascade puede mezclar respetando el ranking
F99-P2.

Binding del usuario (F99.4):

  1. Adapter puro: sin IO, sin db, sin proveedores externos. Recibe el
     consolidated payload ya construido por el consolidator.
  2. Una sola fuente canónica: este adapter NO crea una nueva fuente —
     produce un envelope con ``source="recent_form_consolidated"`` que el
     builder puede sumar al resto sin romper el contrato F74.
  3. Splits oficial vs amistoso: cada ventana se proyecta dos veces —
     una agregada y una por subset (``competition_kind=="official"``)
     para mantener el split sin perder info.
  4. ``l15_is_partial`` siempre que sample_size < 15.

Output shape::

    {
      "source": "recent_form_consolidated",
      "available": True,
      "<side>": {
          "recent_fixtures":      [...]   # L15 (or fewer if partial)
          "goals_scored_l5":      float,
          "goals_conceded_l5":    float,
          "goals_scored_l15":     float,  # only when l15_is_partial=False
          ...
      },
      "field_provenance":    {...},
      "reason_codes":        ["F99_4_RECENT_FORM_CONSOLIDATED", ...],
      "sample_sizes": {
          "<side>.l5_sample_size":  int,
          "<side>.l15_sample_size": int,
          "<side>.l15_is_partial":  bool,
      },
    }

Raw input shape expected (``_recent_form_consolidated_raw``)::

    {
      "home": <consolidated_dict>,
      "away": <consolidated_dict>,
    }

Where each ``<consolidated_dict>`` is the dict returned by
``consolidate_recent_form``.
"""
from __future__ import annotations

from typing import Any, Optional

from ._envelope import (
    RC_MAPPING_OK,
    RC_NO_USABLE_FIELDS,
    RC_RAW_EMPTY,
    RC_RAW_NOT_DICT,
    _safe_mean,
    envelope_unavailable,
    finalize_envelope,
    new_envelope,
    set_field,
)

SOURCE = "recent_form_consolidated"

RC_F99_4_CONSOLIDATED       = "F99_4_RECENT_FORM_CONSOLIDATED"
RC_F99_4_L15_PARTIAL        = "F99_4_L15_PARTIAL"
RC_F99_4_L15_INSUFFICIENT   = "F99_4_L15_INSUFFICIENT"
RC_F99_4_L5_PARTIAL         = "F99_4_L5_PARTIAL"
RC_F99_4_OFFICIAL_FRIENDLY  = "F99_4_OFFICIAL_FRIENDLY_SPLIT"


def _side_payload(raw: Any, side: str) -> Optional[dict]:
    if not isinstance(raw, dict):
        return None
    payload = raw.get(side)
    return payload if isinstance(payload, dict) else None


def _mean(values: list[Any]) -> Optional[float]:
    cleaned = [v for v in values if v is not None]
    if not cleaned:
        return None
    return _safe_mean(cleaned)


def _aggregate_window(rows: list[dict], *, official_only: bool = False) -> dict:
    """Aggregate canonical metrics over a window of consolidated rows."""
    if not rows:
        return {}
    if official_only:
        rows = [r for r in rows if (r.get("competition_kind") == "official")]
        if not rows:
            return {}
    gf  = [r.get("goals_for")        for r in rows]
    ga  = [r.get("goals_against")    for r in rows]
    xgf = [r.get("xg_for")           for r in rows]
    xga = [r.get("xg_against")       for r in rows]
    sh  = [r.get("shots")            for r in rows]
    sot = [r.get("shots_on_target")  for r in rows]
    pos = [r.get("possession")       for r in rows]
    cof = [r.get("corners_for")      for r in rows]
    coa = [r.get("corners_against")  for r in rows]
    return {
        "goals_scored":     _mean(gf),
        "goals_conceded":   _mean(ga),
        "xg_for":           _mean(xgf),
        "xg_against":       _mean(xga),
        "shots_for":        _mean(sh),
        "shots_on_target":  _mean(sot),
        "possession_avg":   _mean(pos),
        "corners_for":      _mean(cof),
        "corners_against":  _mean(coa),
        "sample_size":      len(rows),
    }


def _project_recent_fixtures(rows: list[dict]) -> list[dict]:
    """Pure projection of consolidated rows for the F74 ``recent_fixtures``
    section. We strip internal book-keeping but preserve per-row info that
    the editorial may reference (date, opponent, result, competition_kind).
    """
    out: list[dict] = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        out.append({
            "kickoff_utc":      r.get("kickoff_utc"),
            "opponent":         r.get("opponent_name"),
            "venue":            r.get("venue"),
            "competition":      r.get("competition_name"),
            "competition_kind": r.get("competition_kind"),
            "goals_for":        r.get("goals_for"),
            "goals_against":    r.get("goals_against"),
            "result":           r.get("result"),
        })
    return out


def adapt_recent_form_to_f74(
    raw: Any,
    *,
    home_team: str = "",
    away_team: str = "",
) -> dict:
    """Build the consolidated recent-form F74 envelope.

    Returns an envelope with ``source="recent_form_consolidated"``. The
    cascade will only pick fields when no higher-ranked source already
    has them (since this source is NOT in the per-metric primary slots).
    """
    if raw is None:
        return envelope_unavailable(source=SOURCE, reason=RC_RAW_EMPTY)
    if not isinstance(raw, dict):
        return envelope_unavailable(source=SOURCE, reason=RC_RAW_NOT_DICT)

    env = new_envelope(source=SOURCE, available=True)
    env["sources"]["raw_keys"] = ["home", "away"]

    extra_codes: list[str] = [RC_F99_4_CONSOLIDATED]
    written = 0

    for side in ("home", "away"):
        payload = _side_payload(raw, side)
        if not payload:
            env["field_provenance"][f"{side}._side_skipped"] = {
                "source":       SOURCE,
                "reason_codes": ["SIDE_PAYLOAD_MISSING"],
            }
            continue
        windows = payload.get("windows") or {}
        partial = payload.get("partial_flags") or {}
        recent  = payload.get("recent") or []

        l5  = windows.get("l5")  or []
        l15 = windows.get("l15") or []
        if not l5 and not l15:
            env["field_provenance"][f"{side}._side_skipped"] = {
                "source":       SOURCE,
                "reason_codes": ["EMPTY_WINDOWS"],
            }
            continue

        agg_l5  = _aggregate_window(l5)
        agg_l15 = _aggregate_window(l15)

        l5_n  = len(l5)
        l15_n = len(l15)

        # L5 fields.
        for canonical_name, agg_key in (
            ("goals_scored_l5",     "goals_scored"),
            ("goals_conceded_l5",   "goals_conceded"),
            ("xg_for_l5",           "xg_for"),
            ("xg_against_l5",       "xg_against"),
            ("shots_for_l5",        "shots_for"),
            ("shots_on_target_l5",  "shots_on_target"),
            ("possession_avg_l5",   "possession_avg"),
            ("corners_for_l5",      "corners_for"),
            ("corners_against_l5",  "corners_against"),
        ):
            v = agg_l5.get(agg_key)
            if v is not None:
                if set_field(env, f"{side}.{canonical_name}", v, sample_size=l5_n):
                    written += 1

        # L15 fields — only when the window is non-empty (binding: do not
        # present 10 matches as a complete L15, but DO project the available
        # metrics when downstream cascade asks for them).
        if l15_n >= 1:
            for canonical_name, agg_key in (
                ("goals_scored_l15",   "goals_scored"),
                ("goals_conceded_l15", "goals_conceded"),
                ("xg_for_l15",         "xg_for"),
                ("xg_against_l15",     "xg_against"),
                ("corners_for_l15",    "corners_for"),
                ("corners_against_l15", "corners_against"),
            ):
                v = agg_l15.get(agg_key)
                if v is not None:
                    if set_field(env, f"{side}.{canonical_name}", v, sample_size=l15_n):
                        written += 1

        # Recent fixtures projection (used by editorial).
        proj = _project_recent_fixtures(recent[:15])
        if proj:
            if set_field(env, f"{side}.recent_fixtures", proj,
                          sample_size=min(len(proj), 15)):
                written += 1

        # Official / friendly split (best-effort).
        agg_l5_off = _aggregate_window(l5, official_only=True)
        if agg_l5_off:
            for canonical_name, agg_key in (
                ("goals_scored_l5_official",     "goals_scored"),
                ("goals_conceded_l5_official",   "goals_conceded"),
                ("xg_for_l5_official",           "xg_for"),
                ("xg_against_l5_official",       "xg_against"),
                ("corners_for_l5_official",      "corners_for"),
                ("corners_against_l5_official",  "corners_against"),
            ):
                v = agg_l5_off.get(agg_key)
                if v is not None:
                    if set_field(env, f"{side}.{canonical_name}", v,
                                  sample_size=agg_l5_off.get("sample_size", 0)):
                        written += 1
            extra_codes.append(RC_F99_4_OFFICIAL_FRIENDLY)

        # Sample-size hints (used by the editorial telemetry).
        env["sample_sizes"][f"{side}.l5_sample_size"]  = l5_n
        env["sample_sizes"][f"{side}.l15_sample_size"] = l15_n
        env["sample_sizes"][f"{side}.l5_is_partial"]   = bool(partial.get("l5_is_partial"))
        env["sample_sizes"][f"{side}.l15_is_partial"]  = bool(partial.get("l15_is_partial"))

        if partial.get("l5_is_partial"):
            extra_codes.append(RC_F99_4_L5_PARTIAL)
        if partial.get("l15_is_partial"):
            extra_codes.append(RC_F99_4_L15_PARTIAL)
        if partial.get("l15_insufficient"):
            extra_codes.append(RC_F99_4_L15_INSUFFICIENT)

    if written == 0:
        env["available"] = False
        extra_codes.append(RC_NO_USABLE_FIELDS)
    else:
        extra_codes.append(RC_MAPPING_OK)

    return finalize_envelope(env, extra_codes=extra_codes)


__all__ = [
    "SOURCE",
    "RC_F99_4_CONSOLIDATED",
    "RC_F99_4_L15_PARTIAL",
    "RC_F99_4_L15_INSUFFICIENT",
    "RC_F99_4_L5_PARTIAL",
    "RC_F99_4_OFFICIAL_FRIENDLY",
    "adapt_recent_form_to_f74",
]
