"""Phase F83.1 — Data availability helpers + per-section status.

The legacy ``internal_analysis_debug`` block exposed two contradictory
pieces of information at once:

* ``thestatsapi_found = True``        (because the match doc had a
                                       ``football_data_enrichment`` dict)
* ``missing = [..., "xG", ...]``      (because the ``xg`` sub-block had
                                       no home/away values)

…which led to the UI saying simultaneously "TheStatsAPI / xG disponible"
AND "Faltantes: xG". This module centralises the truth in
``build_data_availability_sections(match)`` so the UI can render
per-section states and never contradict itself again.
"""
from __future__ import annotations

from typing import Any


# ── Atomic availability helpers ──────────────────────────────────────


def has_xg_available(match: dict) -> bool:
    """True iff a *concrete* xG pair (home + away) can be read from the
    match doc. Just having ``football_data_enrichment`` is NOT enough —
    the xG block must carry actual numeric values for both sides."""
    if not isinstance(match, dict):
        return False
    paths = [
        ("football_data_enrichment", "xg"),
        ("thestatsapi_snapshot",     "xg"),
        ("_thestatsapi_enrichment",  "xg"),
    ]
    for root, key in paths:
        block = match.get(root) or {}
        if not isinstance(block, dict):
            continue
        xg = block.get(key)
        if isinstance(xg, dict):
            if xg.get("home") is not None and xg.get("away") is not None:
                return True
    live = match.get("live_stats") or {}
    if isinstance(live, dict):
        if live.get("xg_home") is not None and live.get("xg_away") is not None:
            return True
    return False


def has_thestatsapi_available(match: dict) -> bool:
    """True iff TheStatsAPI enrichment is present on the match (regardless
    of whether xG itself was normalised)."""
    if not isinstance(match, dict):
        return False
    if isinstance(match.get("thestatsapi_snapshot"), dict):
        return True
    if isinstance(match.get("_thestatsapi_enrichment"), dict):
        return True
    fde = match.get("football_data_enrichment")
    if isinstance(fde, dict) and (
        fde.get("source") == "thestatsapi"
        or fde.get("provider") == "thestatsapi"
        or isinstance(fde.get("thestatsapi"), dict)
        # Heuristic: presence of "xg" / "team_stats" suggests TheStatsAPI
        # normalisation ran for this match even when the source label is
        # missing.
        or isinstance(fde.get("xg"), dict)
        or isinstance(fde.get("team_stats"), dict)
    ):
        return True
    return False


def has_h2h_available(match: dict) -> bool:
    """True iff a usable H2H block is present. Accepts both the rich
    F82 ``h2h_context`` block and the legacy ``h2h_recent`` list."""
    if not isinstance(match, dict):
        return False
    ctx = match.get("h2h_context") or {}
    if isinstance(ctx, dict) and ctx.get("available"):
        return True
    rec = match.get("h2h_recent")
    if isinstance(rec, list) and len(rec) > 0:
        return True
    return False


def has_corners_l5_l15_available(match: dict) -> bool:
    """True iff per-team L5/L15 corner averages can be read."""
    if not isinstance(match, dict):
        return False
    # The corner cross block carries the L5/L15 averages we surface in
    # the UI as "corners L5/L15 disponibles".
    cross = (match.get("combined_football_corner_profile_cross")
             or (match.get("footballHistoricalProfile") or {})
                .get("combinedFootballCornerProfileCross")
             or {})
    if isinstance(cross, dict) and cross.get("available"):
        for side in ("home", "away"):
            s = cross.get(side) or {}
            if isinstance(s, dict) and (
                s.get("corners_for_l5")  is not None
                or s.get("corners_for_l15") is not None
            ):
                return True
    return False


def has_market_identity_available(match: dict) -> bool:
    """True iff the engine resolved a concrete market identity
    (non-``UNKNOWN:`` key)."""
    if not isinstance(match, dict):
        return False
    mi = match.get("market_identity")
    if not isinstance(mi, dict):
        return False
    key = mi.get("identity_key")
    if not isinstance(key, str) or not key:
        return False
    return not key.startswith("UNKNOWN:")


def has_recent_form_available(match: dict) -> bool:
    """True iff the recent-form list has at least one fixture for either
    team (the editorial adapter already normalises this)."""
    if not isinstance(match, dict):
        return False
    rf = match.get("recent_fixtures") or []
    if isinstance(rf, list) and len(rf) > 0:
        return True
    # Per-team shape under home_team.context / away_team.context.
    for side in ("home_team", "away_team"):
        s = match.get(side) or {}
        ctx = (s.get("context") if isinstance(s, dict) else None) or {}
        if isinstance(ctx.get("recent_fixtures"), list) and len(ctx["recent_fixtures"]) > 0:
            return True
    return False


# ── Per-section status builder ───────────────────────────────────────


def build_data_availability_sections(match: dict) -> dict:
    """Return a per-section availability map for the editorial debug
    block. The UI MUST use this map (not the legacy boolean flags) to
    decide what to render and what to flag as missing — this avoids the
    "xG disponible AND xG faltante" contradiction.

    Output shape::

        {
          "sections": {
            "thestatsapi":     {"available": true,  "status": "AVAILABLE"},
            "xg":              {"available": false, "status": "MISSING_NORMALIZATION"},
            "h2h":             {"available": true,  "status": "AVAILABLE"},
            "corners":         {"available": false, "status": "MISSING_L5_L15"},
            "market_identity": {"available": false, "status": "REQUIRES_MANUAL_INPUT"},
            "recent_form":     {"available": true,  "status": "AVAILABLE"},
          },
          "available_sections": ["thestatsapi", "h2h", "recent_form"],
          "missing_sections":   ["xG", "corners L5/L15", "market_identity"],
          "missing_codes":      ["XG_NOT_NORMALIZED", "CORNERS_L5_L15_MISSING", "MARKET_IDENTITY_MISSING"],
        }
    """
    has_tsa     = has_thestatsapi_available(match)
    has_xg      = has_xg_available(match)
    has_h2h     = has_h2h_available(match)
    has_corners = has_corners_l5_l15_available(match)
    has_market  = has_market_identity_available(match)
    has_recent  = has_recent_form_available(match)

    sections: dict[str, dict[str, Any]] = {}
    available_sections: list[str] = []
    missing_sections:   list[str] = []
    missing_codes:      list[str] = []

    # ── thestatsapi ──
    sections["thestatsapi"] = {
        "available": has_tsa,
        "status":    "AVAILABLE" if has_tsa else "MISSING",
    }
    if has_tsa:
        available_sections.append("thestatsapi")

    # ── xg ── (the contradiction we are fixing)
    if has_xg:
        sections["xg"] = {"available": True, "status": "AVAILABLE"}
        available_sections.append("xg")
    elif has_tsa:
        # TheStatsAPI present but xG was NOT normalised for this match —
        # surface a specific status so the UI can render the precise
        # message: "TheStatsAPI disponible, xG no normalizado".
        sections["xg"] = {"available": False, "status": "MISSING_NORMALIZATION"}
        missing_sections.append("xG")
        missing_codes.append("XG_NOT_NORMALIZED")
    else:
        sections["xg"] = {"available": False, "status": "MISSING"}
        missing_sections.append("xG")
        missing_codes.append("XG_MISSING")

    # ── h2h ──
    sections["h2h"] = {
        "available": has_h2h,
        "status":    "AVAILABLE" if has_h2h else "MISSING",
    }
    if has_h2h:
        available_sections.append("h2h")
    else:
        missing_sections.append("h2h_recent")
        missing_codes.append("H2H_MISSING")

    # ── corners L5/L15 ──
    sections["corners"] = {
        "available": has_corners,
        "status":    "AVAILABLE" if has_corners else "MISSING_L5_L15",
    }
    if has_corners:
        available_sections.append("corners")
    else:
        missing_sections.append("corners L5/L15")
        missing_codes.append("CORNERS_L5_L15_MISSING")

    # ── market identity ──
    sections["market_identity"] = {
        "available": has_market,
        "status":    "AVAILABLE" if has_market else "REQUIRES_MANUAL_INPUT",
    }
    if has_market:
        available_sections.append("market_identity")
    else:
        missing_sections.append("market_identity")
        missing_codes.append("MARKET_IDENTITY_MISSING")

    # ── recent form ──
    sections["recent_form"] = {
        "available": has_recent,
        "status":    "AVAILABLE" if has_recent else "MISSING",
    }
    if has_recent:
        available_sections.append("recent_form")
    else:
        missing_sections.append("forma reciente")
        missing_codes.append("RECENT_FORM_MISSING")

    return {
        "sections":           sections,
        "available_sections": available_sections,
        "missing_sections":   missing_sections,
        "missing_codes":      missing_codes,
    }


__all__ = [
    "has_xg_available",
    "has_thestatsapi_available",
    "has_h2h_available",
    "has_corners_l5_l15_available",
    "has_market_identity_available",
    "has_recent_form_available",
    "build_data_availability_sections",
]
