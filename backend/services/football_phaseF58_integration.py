"""Football Phase F58 Integration — wires Profile Cross + Player Props
into the football pipeline.

This is a thin **integration helper** that:

  1. Computes L5/L15 averages (goals_for, goals_against, corners)
     from the team's ``recent_fixtures``.
  2. Calls :func:`football_team_profile_cross.compute_combined_football_profile_cross`.
  3. Applies the cross deltas to the recommendation confidence/fragility
     via :func:`apply_profile_cross_to_pick` (with override gating).
  4. Appends a visual entry to ``pick_payload["pattern_alignment"].entries``.
  5. Stores an audit block on ``pick_payload["football_profile_cross_applied"]``.

Fail-soft everywhere — never raises. Caller is the
``attach_football_intelligence_to_payload`` orchestrator.

Override behavior (Phase F58 — confirmed by user)
-------------------------------------------------
When the cross profile is **STRONG_UNDER_CROSS**, **STRONG_OVER_CROSS** or
**CORNERS_OVER_CROSS** **AND** the magnitude is "very strong"
(``confidence_delta >= STRONG_OVERRIDE_THRESHOLD``) AND the current pick
contradicts the cross, the integration writes a soft ``override`` block
on ``pick_payload["football_profile_cross_applied"]`` so the downstream
selector / UI can decide whether to flip the market. The market itself
is **NOT** mutated by this helper — we only emit the recommendation.
This protects the existing market_selection logic from surprise flips
and keeps the override fully auditable.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

log = logging.getLogger("football_phaseF58_integration")


# ─────────────────────────────────────────────────────────────────────
# L5 / L15 derivers from recent_fixtures
# ─────────────────────────────────────────────────────────────────────
def _team_block(side: dict | None) -> dict:
    """Devuelve el sub-bloque con ``recent_fixtures`` para un equipo."""
    if not isinstance(side, dict):
        return {}
    ctx = side.get("context") if isinstance(side.get("context"), dict) else side
    return ctx or {}


def _slice_avg(values: list, k: int) -> Optional[float]:
    """Promedia los primeros ``k`` valores numéricos (recent_fixtures viene
    newest-first). Devuelve ``None`` si no hay datos suficientes.
    """
    if not values:
        return None
    nums: list[float] = []
    for v in values[:k]:
        try:
            nums.append(float(v))
        except (TypeError, ValueError):
            continue
    if not nums:
        return None
    return round(sum(nums) / len(nums), 3)


def _derive_l5_l15_from_recent(side: dict | None) -> dict:
    """Construye el dict de inputs L5/L15 que espera
    ``compute_combined_football_profile_cross``.

    Lee de ``recent_fixtures`` (lista de matches) los arrays ``gf``/``ga``
    si vienen pre-normalizados, o calcula desde dicts individuales.
    Corners se extraen si vienen en cada fixture.
    """
    blk = _team_block(side)
    recent = blk.get("recent_fixtures") or blk.get("last_matches") or {}

    gf_list: list = []
    ga_list: list = []
    corners_list: list = []
    shots_list: list = []
    sot_list: list = []

    # Caso A: recent_fixtures es un dict pre-normalizado con arrays.
    if isinstance(recent, dict):
        gf_list = list(recent.get("gf") or [])
        ga_list = list(recent.get("ga") or [])
        corners_list = list(recent.get("corners_for") or recent.get("corners") or [])
        shots_list   = list(recent.get("shots") or [])
        sot_list     = list(recent.get("shots_on_target") or recent.get("sot") or [])
    # Caso B: lista de fixtures crudos.
    elif isinstance(recent, list):
        for f in recent:
            if not isinstance(f, dict):
                continue
            gf = f.get("gf") if "gf" in f else f.get("goals_for")
            ga = f.get("ga") if "ga" in f else f.get("goals_against")
            if gf is not None:
                gf_list.append(gf)
            if ga is not None:
                ga_list.append(ga)
            co = f.get("corners_for") or (f.get("corners") if isinstance(f.get("corners"), (int, float)) else None)
            if co is not None:
                corners_list.append(co)
            sh = f.get("shots_total") or f.get("shots")
            if sh is not None:
                shots_list.append(sh)
            sot = f.get("shots_on_target") or f.get("sot")
            if sot is not None:
                sot_list.append(sot)

    out: dict[str, Any] = {
        "goals_for_l5":     _slice_avg(gf_list, 5),
        "goals_for_l15":    _slice_avg(gf_list, 15),
        "goals_against_l5": _slice_avg(ga_list, 5),
        "goals_against_l15": _slice_avg(ga_list, 15),
        "corners_l5":       _slice_avg(corners_list, 5),
        "corners_l15":      _slice_avg(corners_list, 15),
        "shots_l5":         _slice_avg(shots_list, 5),
        "shots_l15":        _slice_avg(shots_list, 15),
        "sot_l5":           _slice_avg(sot_list, 5),
        "sot_l15":          _slice_avg(sot_list, 15),
    }

    # xG / xGA opcional desde un bloque Understat ya hidratado.
    us = side.get("_understat") if isinstance(side, dict) else None
    if isinstance(us, dict):
        # Estructura conservadora: si vienen agregados como xg_l5/l15 los usamos.
        for k_in, k_out in (
            ("xg_l5", "xg_l5"), ("xg_l15", "xg_l15"),
            ("xga_l5", "xga_l5"), ("xga_l15", "xga_l15"),
        ):
            v = us.get(k_in)
            if v is not None:
                try:
                    out[k_out] = float(v)
                except (TypeError, ValueError):
                    pass

    return out


# ─────────────────────────────────────────────────────────────────────
# Pick-side resolver
# ─────────────────────────────────────────────────────────────────────
def _resolve_pick_side(recommendation: dict | None) -> Optional[str]:
    """Devuelve la categoría del pick: 'OVER', 'UNDER', 'BTTS',
    'CORNERS' o el market upper case si es otra cosa (ML, etc.).
    """
    if not isinstance(recommendation, dict):
        return None
    market = (recommendation.get("market") or "").upper()
    selection = (recommendation.get("selection") or "").upper()
    full = f"{market} {selection}".strip()
    if "OVER" in full and "CORNER" in full:
        return "CORNERS"
    if "OVER" in full:
        return "OVER"
    if "UNDER" in full:
        return "UNDER"
    if "BTTS" in full or "BOTH TEAMS" in full:
        return "BTTS"
    if "CORNER" in full:
        return "CORNERS"
    return market or None


# ─────────────────────────────────────────────────────────────────────
# Public entry
# ─────────────────────────────────────────────────────────────────────
def attach_football_profile_cross_to_payload(
    pick_payload: dict | None,
    match: dict | None,
    *,
    allow_override: bool = True,
) -> dict:
    """Calcula y aplica el cross profile sobre el pick. Mutates
    ``pick_payload``.

    Returns an audit dict::

        {
            "available":   bool,
            "profile":     str | None,
            "supports":    str,
            "applied":     bool,
            "interaction": str,
            "override":    dict | None,
        }
    """
    if not isinstance(pick_payload, dict):
        return {"available": False, "_reason": "no_pick_payload"}
    if not isinstance(match, dict):
        match = {}

    try:
        from services.football_team_profile_cross import (
            compute_combined_football_profile_cross,
            apply_profile_cross_to_pick,
            build_pattern_alignment_entry,
        )
    except Exception as exc:  # noqa: BLE001
        log.debug("football_team_profile_cross import failed: %s", exc)
        return {"available": False, "_reason": "module_unavailable", "error": str(exc)}

    home = match.get("home_team") or match.get("home") or {}
    away = match.get("away_team") or match.get("away") or {}

    home_inputs = _derive_l5_l15_from_recent(home)
    away_inputs = _derive_l5_l15_from_recent(away)

    try:
        cross = compute_combined_football_profile_cross(
            home=home_inputs,
            away=away_inputs,
        )
    except Exception as exc:  # noqa: BLE001
        log.debug("compute_combined_football_profile_cross failed: %s", exc)
        return {"available": False, "_reason": "compute_failed", "error": str(exc)}

    pick_payload["combined_football_profile_cross"] = cross

    # Mirror into footballHistoricalProfile camelCase (UI convenience).
    fhp = pick_payload.get("footballHistoricalProfile") or {}
    fhp["combinedFootballProfileCross"] = cross
    pick_payload["footballHistoricalProfile"] = fhp

    if not cross.get("available"):
        return {
            "available":   False,
            "profile":     None,
            "supports":    "NEUTRAL",
            "applied":     False,
            "interaction": "SKIPPED",
            "override":    None,
            "_reason":     cross.get("_skipped_reason", "unavailable"),
        }

    # Skip the apply step entirely when supports is NEUTRAL.
    if cross.get("supports") == "NEUTRAL":
        return {
            "available":   True,
            "profile":     cross.get("profile"),
            "supports":    "NEUTRAL",
            "applied":     False,
            "interaction": "NEUTRAL",
            "override":    None,
        }

    rec = pick_payload.get("recommendation") or {}
    side = _resolve_pick_side(rec)
    conf = rec.get("confidence_score")
    frag = (pick_payload.get("fragility") or {}).get("score") \
        or pick_payload.get("fragility_score")

    try:
        applied = apply_profile_cross_to_pick(
            cross_payload=cross,
            pick_side=side,
            pick_market=(rec.get("market") if isinstance(rec, dict) else None),
            current_confidence=conf,
            current_fragility=frag,
            allow_override=allow_override,
        )
    except Exception as exc:  # noqa: BLE001
        log.debug("apply_profile_cross_to_pick failed: %s", exc)
        return {
            "available":   True,
            "profile":     cross.get("profile"),
            "supports":    cross.get("supports"),
            "applied":     False,
            "interaction": "ERROR",
            "override":    None,
            "error":       str(exc),
        }

    # Write back clamped confidence/fragility.
    if applied.get("applied"):
        if applied.get("new_confidence") is not None and isinstance(rec, dict):
            rec["confidence_score"] = round(float(applied["new_confidence"]), 2)
            rec.setdefault("reason_codes", [])
            for _rc in applied.get("reason_codes") or []:
                if _rc not in rec["reason_codes"]:
                    rec["reason_codes"].append(_rc)
            pick_payload["recommendation"] = rec
        if applied.get("new_fragility") is not None:
            if not isinstance(pick_payload.get("fragility"), dict):
                pick_payload["fragility"] = {}
            pick_payload["fragility"]["score"] = float(applied["new_fragility"])
            pick_payload["fragility"]["source"] = "football_profile_cross"
            pick_payload["fragility_score"] = float(applied["new_fragility"])

        pick_payload["football_profile_cross_applied"] = {
            "profile":                  cross.get("profile"),
            "supports":                 cross.get("supports"),
            "interaction":              applied.get("interaction"),
            "confidence_delta_signed":  applied.get("confidence_delta_signed"),
            "fragility_delta_signed":   applied.get("fragility_delta_signed"),
            "pick_side":                side,
            "reason_codes":             applied.get("reason_codes") or [],
            "override":                 applied.get("override"),
        }

    # Visual-only entry in pattern_alignment.entries.
    try:
        entry = build_pattern_alignment_entry(cross, side)
        if entry:
            pa = pick_payload.get("pattern_alignment") or {}
            entries = list(pa.get("entries") or [])
            entries.append(entry)
            pa["entries"] = entries
            pick_payload["pattern_alignment"] = pa
    except Exception as exc:  # noqa: BLE001
        log.debug("build_pattern_alignment_entry failed: %s", exc)

    return {
        "available":   True,
        "profile":     cross.get("profile"),
        "supports":    cross.get("supports"),
        "applied":     bool(applied.get("applied")),
        "interaction": applied.get("interaction"),
        "override":    applied.get("override"),
    }


__all__ = [
    "attach_football_profile_cross_to_payload",
    "_derive_l5_l15_from_recent",
    "_resolve_pick_side",
]
