"""Phase F66 — Football Internal Editorial Prediction Engine.

This module REPLACES the runtime dependency on Scores24 by generating an
internal, dynamic editorial preview of every football match that goes
through the discard pipeline. Scores24, when reachable, is kept as a
*secondary* enrichment source — never a blocker.

Five sub-sections produced (Phase F66 MVP = 4 + H2H placeholder):

    1. ``corners_prediction``  — uses football_corner_profile_cross +
       L5/L15 corner volumes and the rules the user spelled out.
    2. ``goals_prediction``    — uses football_team_profile_cross +
       football_under_support + football_over_support + xG.
    3. ``key_trends``          — top-5 trends extracted from L5/L15 data
       (BTTS rate, clean sheets, under/over rate, scoring drought, etc.).
    4. ``head_to_head``        — placeholder until H2H collection exists
       in Mongo; returns ``available=false`` cleanly otherwise.
    5. ``probable_score``      — Dixon-Coles / Poisson / heuristic-by-
       profile cascade from :mod:`services.football_dixon_coles`.

Public entry-point::

    out = generate_football_editorial_prediction(match_payload,
                                                 odds=None,            # TheStatsAPI normalised
                                                 h2h_matches=None)     # list[dict] | None

Returns a JSON-serialisable dict that the FastAPI endpoint and the
``compute_structural_value_review`` orchestrator can attach to every
pick they emit.

ALL paths are fail-soft. Missing data → ``available=False`` + ``status:
"MISSING"`` for that sub-section. The module never raises.
"""
from __future__ import annotations

import logging
import statistics
from typing import Any, Optional

log = logging.getLogger("football.editorial_prediction")

ENGINE_VERSION = "football_editorial_prediction.v1"
SOURCE_TAG     = "internal_engine"

# ─────────────────────────────────────────────────────────────────────
# Tunables
# ─────────────────────────────────────────────────────────────────────
LOW_CORNER_VOLUME    = 4.0
HIGH_CORNER_VOLUME   = 5.5
LOW_GOAL_VOLUME      = 1.10
HIGH_GOAL_VOLUME     = 1.70
MAX_KEY_TRENDS       = 5


# ─────────────────────────────────────────────────────────────────────
# Tiny helpers
# ─────────────────────────────────────────────────────────────────────
def _safe(v: Any) -> Optional[float]:
    try:
        f = float(v)
        return f
    except (TypeError, ValueError):
        return None


def _data_completeness(match: dict) -> dict:
    """Phase F67 — audit which data sources are available for the
    editorial engine. Used to inject cautious phrasing when the report
    is built on a thin payload.

    Returns a dict with booleans per source and a derived
    ``completeness`` string ("FULL", "PARTIAL", "THIN").
    """
    if not isinstance(match, dict):
        return {
            "has_corners_l5":     False,
            "has_goals_history":  False,
            "has_xg":             False,
            "has_btts_rate":      False,
            "has_clean_sheets":   False,
            "has_h2h":            False,
            "completeness":       "THIN",
            "available_sources":  [],
        }

    home_t = match.get("home_team") if isinstance(match.get("home_team"), dict) else {}
    away_t = match.get("away_team") if isinstance(match.get("away_team"), dict) else {}

    has_corners_l5 = bool(
        _safe(match.get("home_corners_for_l5")) is not None
        and _safe(match.get("away_corners_for_l5")) is not None
    )
    has_goals_history = bool(
        _safe(home_t.get("goals_scored_l5")) is not None
        or _safe(home_t.get("goals_scored_l15")) is not None
        or _safe(match.get("home_goals_scored_l5")) is not None
    )
    has_xg = bool(
        _safe(match.get("home_xg")) is not None
        and _safe(match.get("away_xg")) is not None
    )
    has_btts = bool(
        _safe(home_t.get("btts_rate_l15")) is not None
        or _safe(away_t.get("btts_rate_l15")) is not None
    )
    has_clean_sheets = bool(
        _safe(home_t.get("clean_sheet_rate_l15")) is not None
        or _safe(away_t.get("clean_sheet_rate_l15")) is not None
    )

    sources = []
    if has_corners_l5:    sources.append("corners L5/L15")
    if has_goals_history: sources.append("historial de goles L5/L15")
    if has_xg:            sources.append("xG / xGA")
    if has_btts:          sources.append("BTTS rate L15")
    if has_clean_sheets:  sources.append("porterías a cero L15")

    n_sources = sum([has_corners_l5, has_goals_history, has_xg, has_btts, has_clean_sheets])
    if n_sources >= 4:
        completeness = "FULL"
    elif n_sources >= 2:
        completeness = "PARTIAL"
    else:
        completeness = "THIN"

    return {
        "has_corners_l5":     has_corners_l5,
        "has_goals_history":  has_goals_history,
        "has_xg":             has_xg,
        "has_btts_rate":      has_btts,
        "has_clean_sheets":   has_clean_sheets,
        "has_h2h":            False,        # injected externally by caller
        "completeness":       completeness,
        "available_sources":  sources,
    }


def _cautious_prefix(completeness: str, sources: list) -> str:
    """Return a cautious prefix when data is partial / thin.

    Phase F67 — when ``completeness != FULL`` the editorial narrative MUST
    avoid absolute claims like "El historial confirma..." and instead
    open with "Con los datos disponibles (X, Y)..." or
    "Con la información parcial disponible...".
    """
    if completeness == "FULL":
        return ""
    if completeness == "PARTIAL" and sources:
        return f"Con los datos disponibles ({', '.join(sources[:3])}), "
    if completeness == "PARTIAL":
        return "Con la información parcial disponible, "
    return "Con los datos limitados disponibles, "


def _team_name(match: dict, side: str) -> str:
    if not isinstance(match, dict):
        return side.title()
    key = "home_team" if side == "home" else "away_team"
    val = match.get(key) or match.get(side)
    if isinstance(val, dict):
        return val.get("name") or val.get("label") or side.title()
    if isinstance(val, str):
        return val
    return side.title()


def _avg(*vals: Optional[float]) -> Optional[float]:
    nums = [v for v in vals if isinstance(v, (int, float))]
    if not nums:
        return None
    return round(sum(nums) / len(nums), 2)


# ─────────────────────────────────────────────────────────────────────
# 1. CORNERS PREDICTION
# ─────────────────────────────────────────────────────────────────────
def _build_corners_prediction(match: dict, odds: Optional[dict],
                                completeness: str = "FULL",
                                available_sources: Optional[list] = None) -> dict:
    home_name = _team_name(match, "home")
    away_name = _team_name(match, "away")

    try:
        from services.football_corner_profile_cross import (
            compute_football_corner_profile_cross,
            extract_corner_side_from_match,
        )
        home_side = extract_corner_side_from_match(match, "home")
        away_side = extract_corner_side_from_match(match, "away")
        cross = compute_football_corner_profile_cross(
            home=home_side, away=away_side, scores24_payload=None,
        ) or {}
    except Exception as exc:  # noqa: BLE001
        log.debug("[F66_CORNERS] cross failed: %s", exc)
        cross = {}

    if not cross.get("available"):
        return {
            "available":     False,
            "status":        "MISSING",
            "title":         "Predicción sobre córners",
            "text":          ("No hay suficientes datos de córners L5/L15 "
                              "para emitir una recomendación fiable."),
            "reason_codes":  ["CORNERS_PREDICTION_INSUFFICIENT_DATA"],
        }

    profile  = cross.get("profile")
    supports = cross.get("supports")
    # Pull volumes back out so the narrative can reference real numbers.
    home_cf5 = _safe(match.get("home_corners_for_l5"))
    away_cf5 = _safe(match.get("away_corners_for_l5"))
    home_ca5 = _safe(match.get("home_corners_against_l5"))
    away_ca5 = _safe(match.get("away_corners_against_l5"))
    total_l5 = _avg(home_cf5, away_cf5)

    # Pick the recommended market + line + odds.
    side, market, line, mkt_odds, confidence = _resolve_corner_market(
        supports, odds, total_l5,
    )

    if side == "WATCHLIST":
        text = (
            f"Los perfiles de córners de {home_name} y {away_name} son "
            f"mixtos, sin convergencia clara hacia Over o Under. No "
            "recomendamos forzar este mercado: queda en watchlist."
        )
        reason_codes = [
            "INTERNAL_CORNERS_PREDICTION_GENERATED",
            "MIXED_CORNERS_PROFILE_NO_RECOMMENDATION",
        ]
        return {
            "available":            True,
            "status":               "WATCHLIST",
            "title":                "Predicción sobre córners",
            "text":                 text,
            "recommended_market":   None,
            "market_type":          "corners_total",
            "side":                 "WATCHLIST",
            "line":                 None,
            "odds":                 None,
            "confidence":           35,
            "profile":              profile,
            "reason_codes":         reason_codes,
        }

    text = _corner_narrative(
        home_name, away_name, profile, supports, side,
        home_cf5, away_cf5, home_ca5, away_ca5, market, mkt_odds,
        completeness=completeness, available_sources=available_sources,
    )

    return {
        "available":            True,
        "status":               "OK",
        "title":                "Predicción sobre córners",
        "text":                 text,
        "recommended_market":   market,
        "market_type":          "corners_total" if side in ("UNDER", "OVER")
                                else "team_corners",
        "side":                 side,
        "line":                 line,
        "odds":                 mkt_odds,
        "confidence":           confidence,
        "profile":              profile,
        "reason_codes":         [
            "INTERNAL_CORNERS_PREDICTION_GENERATED",
            f"CORNERS_PROFILE_SUPPORTS_{side}",
        ],
    }


def _resolve_corner_market(
    supports: Optional[str], odds: Optional[dict],
    total_l5: Optional[float],
) -> tuple[str, Optional[str], Optional[float], Optional[float], int]:
    """Return (side, market_label, line, odds, confidence)."""
    if not supports:
        return ("WATCHLIST", None, None, None, 35)

    if supports == "CORNERS_UNDER":
        # Canonical Under line picked from TheStatsAPI normalised odds.
        line, mkt_odds = _pick_corner_line(odds, side="under",
                                            preferred_lines=(9.5, 10.5, 11.5))
        market = f"Under {line} córners" if line is not None else "Under córners"
        return ("UNDER", market, line, mkt_odds, 64)
    if supports == "CORNERS_OVER":
        line, mkt_odds = _pick_corner_line(odds, side="over",
                                            preferred_lines=(9.5, 10.5, 11.5))
        market = f"Over {line} córners" if line is not None else "Over córners"
        return ("OVER", market, line, mkt_odds, 64)
    if supports == "TEAM_CORNERS_OVER":
        # Team corners over does not have a canonical TheStatsAPI line in
        # the basic market_corners block; we surface it without an odd.
        return ("TEAM_OVER", "Team corners Over", 5.5, None, 58)
    return ("WATCHLIST", None, None, None, 35)


def _pick_corner_line(
    odds: Optional[dict], *, side: str,
    preferred_lines: tuple[float, ...],
) -> tuple[Optional[float], Optional[float]]:
    """Walk the preferred lines in order and return the first that has odds."""
    if not isinstance(odds, dict):
        return (preferred_lines[0], None)
    mc = (odds.get("match_corners") or {})
    for line in preferred_lines:
        node = mc.get(str(line))
        if isinstance(node, dict):
            v = node.get(side)
            if v is not None:
                return (line, _safe(v))
    return (preferred_lines[0], None)


def _corner_narrative(home, away, profile, supports, side,
                       h_cf5, a_cf5, h_ca5, a_ca5, market, odds,
                       completeness="FULL", available_sources=None) -> str:
    parts: list[str] = []
    prefix = _cautious_prefix(completeness, available_sources or [])
    if side == "UNDER":
        # Phase F67 — only state precise volume claims when we have L5.
        if h_cf5 is not None and a_cf5 is not None:
            parts.append(
                f"{prefix}{home} promedia {h_cf5:.1f} córners por partido "
                f"en sus últimos 5 encuentros y {away} apenas {a_cf5:.1f}."
            )
        else:
            parts.append(
                f"{prefix}{home} y {away} muestran perfiles de bajo "
                "volumen de córners."
            )
        # Avoid absolute "confirma" claims when data is partial.
        if completeness == "FULL":
            parts.append(
                "El cruce de perfiles confirma un partido de bajo volumen "
                "ofensivo por las bandas, con poca presencia en el área "
                "rival de ambos equipos."
            )
        else:
            parts.append(
                "El cruce de perfiles sugiere un partido de bajo volumen "
                "ofensivo por las bandas; conviene contrastar con más "
                "fuentes antes de decidir."
            )
        parts.append(f"Mercado sugerido: {market}"
                     + (f" a cuota {odds:.2f}*." if odds else "*."))
    elif side == "OVER":
        if h_cf5 is not None and a_cf5 is not None:
            parts.append(
                f"{prefix}{home} genera {h_cf5:.1f} córners por partido "
                f"(L5) y {away} otros {a_cf5:.1f}, con perfiles cruzados "
                "que se exponen mutuamente."
            )
        else:
            parts.append(
                f"{prefix}{home} y {away} dejan un perfil ofensivo por "
                "bandas elevado."
            )
        if h_ca5 is not None and a_ca5 is not None and completeness == "FULL":
            parts.append(
                f"Además ambos defienden con poca compactación "
                f"({home} concede {h_ca5:.1f} y {away} concede "
                f"{a_ca5:.1f} córners por partido)."
            )
        parts.append(f"Mercado sugerido: {market}"
                     + (f" a cuota {odds:.2f}*." if odds else "*."))
    elif side == "TEAM_OVER":
        parts.append(
            f"{prefix}hay asimetría clara: uno de los dos equipos genera "
            "mucho volumen por bandas mientras el rival concede en "
            f"cantidad. Mercado sugerido: {market}."
        )
    return " ".join(parts)


# ─────────────────────────────────────────────────────────────────────
# 2. GOALS PREDICTION
# ─────────────────────────────────────────────────────────────────────
def _build_goals_prediction(match: dict, odds: Optional[dict],
                              completeness: str = "FULL",
                              available_sources: Optional[list] = None) -> dict:
    home_name = _team_name(match, "home")
    away_name = _team_name(match, "away")

    # Sub-engines (all fail-soft).
    try:
        from services.football_team_profile_cross import (
            compute_football_team_profile_cross,
        )
        team_cross = compute_football_team_profile_cross(
            home=match.get("home_team") or match.get("home"),
            away=match.get("away_team") or match.get("away"),
        ) or {}
    except Exception as exc:  # noqa: BLE001
        log.debug("[F66_GOALS] team_profile_cross failed: %s", exc)
        team_cross = {"available": False}
    try:
        from services.football_under_support import compute_football_under_support
        under_sup = compute_football_under_support(match) or {}
    except Exception as exc:  # noqa: BLE001
        log.debug("[F66_GOALS] under_support failed: %s", exc)
        under_sup = {"available": False}
    try:
        from services.football_over_support import compute_football_over_support
        over_sup = compute_football_over_support(match) or {}
    except Exception as exc:  # noqa: BLE001
        log.debug("[F66_GOALS] over_support failed: %s", exc)
        over_sup = {"available": False}

    profile = (team_cross.get("profile") or "").upper()
    supports = team_cross.get("supports") or ""
    u_score = int(under_sup.get("score") or 0)
    o_score = int(over_sup.get("score") or 0)

    home_xg = _safe((match.get("home_xg")
                     if isinstance(match, dict) else None)
                    or (match.get("home_team") if isinstance(match.get("home_team"), dict) else {}).get("xg_avg"))
    away_xg = _safe((match.get("away_xg")
                     if isinstance(match, dict) else None)
                    or (match.get("away_team") if isinstance(match.get("away_team"), dict) else {}).get("xg_avg"))

    side, market, line, mkt_odds, confidence, reason_codes = _resolve_goals_market(
        profile, supports, u_score, o_score, home_xg, away_xg, odds,
    )

    if side == "WATCHLIST":
        text = (f"El cruce de perfiles ofensivos y defensivos entre {home_name} "
                f"y {away_name} no apunta claramente a Over ni Under. "
                "Mercado de goles queda en watchlist.")
        return {
            "available":            True,
            "status":               "WATCHLIST",
            "title":                "Predicción de goles Over/Under",
            "text":                 text,
            "recommended_market":   None,
            "market_type":          "goals_total",
            "side":                 "WATCHLIST",
            "line":                 None,
            "odds":                 None,
            "confidence":           35,
            "profile":              profile or None,
            "reason_codes":         reason_codes,
        }

    text = _goals_narrative(home_name, away_name, side, profile,
                             u_score, o_score, market, mkt_odds, home_xg, away_xg,
                             completeness=completeness,
                             available_sources=available_sources)

    return {
        "available":            True,
        "status":               "OK",
        "title":                "Predicción de goles Over/Under",
        "text":                 text,
        "recommended_market":   market,
        "market_type":          "goals_total",
        "side":                 side,
        "line":                 line,
        "odds":                 mkt_odds,
        "confidence":           confidence,
        "profile":              profile or None,
        "reason_codes":         reason_codes,
    }


def _resolve_goals_market(profile, supports, u_score, o_score,
                          home_xg, away_xg, odds):
    """Map the profile + scores into a goals market recommendation."""
    reasons = ["INTERNAL_GOALS_PREDICTION_GENERATED"]
    # Tier 1 — clear UNDER signal.
    if "LOW_EVENT" in (profile or "") or u_score >= 60:
        reasons.append("LOW_EVENT_UNDER_CROSS")
        if u_score >= 60:
            reasons.append("UNDER_SUPPORT_CONFIRMS")
        line, mkt_odds = _pick_goal_line(odds, side="under",
                                          preferred_lines=(2.5, 3.5, 1.5))
        market = f"Under {line} goles"
        confidence = 70 if u_score >= 60 else 60
        return ("UNDER", market, line, mkt_odds, confidence, reasons)
    # Tier 2 — clear OVER signal (cautious — Over 1.5 preferred over Over 2.5).
    if "HIGH_EVENT" in (profile or "") or o_score >= 60:
        reasons.append("HIGH_EVENT_OVER_CROSS")
        if o_score >= 60:
            reasons.append("OVER_SUPPORT_CONFIRMS")
        line, mkt_odds = _pick_goal_line(odds, side="over",
                                          preferred_lines=(1.5, 2.5))
        market = f"Over {line} goles"
        confidence = 62 if o_score >= 60 else 55
        return ("OVER", market, line, mkt_odds, confidence, reasons)
    # Tier 3 — BTTS only when team_cross is explicit.
    if supports == "BTTS":
        reasons.append("BILATERAL_THREAT_BTTS_VALID")
        return ("BTTS_YES", "BTTS Yes", None,
                _btts_odds(odds, "yes"), 55, reasons)
    return ("WATCHLIST", None, None, None, 35,
            reasons + ["NO_CLEAR_GOALS_VALUE"])


def _pick_goal_line(odds, *, side, preferred_lines):
    if not isinstance(odds, dict):
        return (preferred_lines[0], None)
    tg = odds.get("total_goals") or {}
    for line in preferred_lines:
        node = tg.get(str(line))
        if isinstance(node, dict):
            v = node.get(side)
            if v is not None:
                return (line, _safe(v))
    return (preferred_lines[0], None)


def _btts_odds(odds, side):
    if not isinstance(odds, dict):
        return None
    bt = odds.get("btts") or {}
    return _safe(bt.get(side))


def _goals_narrative(home, away, side, profile, u_score, o_score,
                      market, odds, home_xg, away_xg,
                      completeness="FULL", available_sources=None) -> str:
    parts: list[str] = []
    prefix = _cautious_prefix(completeness, available_sources or [])
    if side == "UNDER":
        parts.append(
            f"{prefix}{away} sabe jugar replegado y puede limitar el "
            f"potencial ofensivo de {home}."
        )
        if home_xg is not None:
            parts.append(f"{home} promedia {home_xg:.2f} xG por partido.")
        if u_score >= 60 and completeness == "FULL":
            parts.append(f"El score de soporte Under ({u_score}/100) confirma el perfil.")
        elif u_score >= 60:
            parts.append(f"El score de soporte Under ({u_score}/100) apunta a esa lectura.")
        parts.append(f"De ahí que resulte atractivo el mercado {market}"
                     + (f" con una cuota cercana a {odds:.2f}*." if odds else "*."))
    elif side == "OVER":
        if home_xg is not None and away_xg is not None:
            parts.append(f"{prefix}ambos equipos llegan con buen perfil "
                         f"ofensivo ({home}: {home_xg:.2f} xG, "
                         f"{away}: {away_xg:.2f} xG).")
        else:
            parts.append(f"{prefix}el cruce sugiere un partido de "
                         "perfil ofensivo elevado.")
        verb = "apoya" if completeness == "FULL" else "sugiere"
        parts.append(f"El score de soporte Over ({o_score}/100) {verb} un "
                     f"partido con goles. Mercado sugerido: {market}"
                     + (f" a cuota {odds:.2f}*." if odds else "*."))
    elif side == "BTTS_YES":
        parts.append(
            f"{prefix}amenaza bilateral real: {home} y {away} tienen "
            f"recursos para marcar y conceder. Mercado sugerido: BTTS Yes"
            + (f" a cuota {odds:.2f}*." if odds else "*.")
        )
    return " ".join(parts)


# ─────────────────────────────────────────────────────────────────────
# 3. KEY TRENDS
# ─────────────────────────────────────────────────────────────────────
def _build_key_trends(match: dict, recommended_side_corners: Optional[str],
                       recommended_side_goals: Optional[str]) -> dict:
    home_name = _team_name(match, "home")
    away_name = _team_name(match, "away")

    home = match.get("home_team") if isinstance(match.get("home_team"), dict) else {}
    away = match.get("away_team") if isinstance(match.get("away_team"), dict) else {}

    items: list[dict] = []   # {text, priority, side ("under"/"over"/"corners-under"/...) }

    # Goals scored L5
    h_g5 = _safe(home.get("goals_scored_l5") or match.get("home_goals_scored_l5"))
    a_g5 = _safe(away.get("goals_scored_l5") or match.get("away_goals_scored_l5"))
    if h_g5 is not None and h_g5 <= 1.20:
        items.append({"text": f"{home_name} no superó los 1.5 goles de promedio en sus últimos 5 partidos ({h_g5:.2f}).",
                      "priority": 3, "side": "goals_under"})
    if a_g5 is not None and a_g5 <= 1.20:
        items.append({"text": f"{away_name} promedia {a_g5:.2f} goles en sus últimos 5 partidos (perfil bajo).",
                      "priority": 3, "side": "goals_under"})
    if h_g5 is not None and h_g5 >= 2.0:
        items.append({"text": f"{home_name} promedia {h_g5:.2f} goles por partido en los últimos 5 (perfil alto).",
                      "priority": 3, "side": "goals_over"})

    # BTTS rate
    btts_rate_h = _safe(home.get("btts_rate_l15"))
    btts_rate_a = _safe(away.get("btts_rate_l15"))
    if btts_rate_h is not None and btts_rate_a is not None:
        joint = (btts_rate_h + btts_rate_a) / 2
        if joint >= 0.65:
            items.append({"text": f"Ambos equipos marcaron en el {int(joint*100)}% de los últimos 15 partidos (perfil BTTS Yes).",
                          "priority": 2, "side": "btts"})
        elif joint <= 0.35:
            items.append({"text": f"Solo en el {int(joint*100)}% de los últimos 15 partidos hubo BTTS Yes (perfil cerrado).",
                          "priority": 2, "side": "goals_under"})

    # Corners L5
    h_cf5 = _safe(match.get("home_corners_for_l5"))
    a_cf5 = _safe(match.get("away_corners_for_l5"))
    if h_cf5 is not None and h_cf5 <= LOW_CORNER_VOLUME:
        items.append({"text": f"{home_name} no superó los 4 córners de promedio en sus últimos 5 partidos.",
                      "priority": 3, "side": "corners_under"})
    if a_cf5 is not None and a_cf5 <= LOW_CORNER_VOLUME:
        items.append({"text": f"{away_name} se mantuvo por debajo de 4 córners por partido en sus últimos 5.",
                      "priority": 3, "side": "corners_under"})
    if h_cf5 is not None and h_cf5 >= HIGH_CORNER_VOLUME:
        items.append({"text": f"{home_name} promedia más de 5.5 córners por partido (perfil ofensivo alto por bandas).",
                      "priority": 3, "side": "corners_over"})

    # Clean sheets
    cs_h = _safe(home.get("clean_sheet_rate_l15"))
    if cs_h is not None and cs_h >= 0.45:
        items.append({"text": f"{home_name} mantuvo portería a cero en el {int(cs_h*100)}% de los últimos 15 partidos.",
                      "priority": 2, "side": "goals_under"})

    # Filter/dedup + prioritise items that support the recommended markets.
    if not items:
        return {
            "available":     False,
            "status":        "MISSING",
            "title":         "Tendencias clave",
            "items":         [],
            "reason_codes":  ["KEY_TRENDS_INSUFFICIENT_DATA"],
        }

    def supports_recommendation(it: dict) -> int:
        side = it["side"]
        score = it["priority"]
        if recommended_side_goals == "UNDER" and side == "goals_under":
            score += 2
        if recommended_side_goals == "OVER"  and side == "goals_over":
            score += 2
        if recommended_side_corners == "UNDER" and side == "corners_under":
            score += 2
        if recommended_side_corners == "OVER"  and side == "corners_over":
            score += 2
        return score

    items.sort(key=supports_recommendation, reverse=True)
    return {
        "available":     True,
        "status":        "OK",
        "title":         "Tendencias clave",
        "items":         [i["text"] for i in items[:MAX_KEY_TRENDS]],
        "reason_codes":  ["KEY_TRENDS_GENERATED"],
    }


# ─────────────────────────────────────────────────────────────────────
# 4. HEAD-TO-HEAD (placeholder until we wire a real H2H source)
# ─────────────────────────────────────────────────────────────────────
def _build_head_to_head(match: dict, h2h_matches: Optional[list[dict]]) -> dict:
    if not h2h_matches:
        return {
            "available":     False,
            "status":        "MISSING",
            "title":         "Enfrentamientos directos",
            "text":          ("No hay enfrentamientos recientes suficientes "
                              "para una lectura fuerte."),
            "matches_found": 0,
            "items":         [],
            "reason_codes":  ["H2H_INSUFFICIENT_SAMPLE"],
        }
    items: list[dict] = []
    for h in h2h_matches[:5]:
        if not isinstance(h, dict):
            continue
        items.append({
            "date":      h.get("date") or h.get("utc_date"),
            "home_team": h.get("home_team") or h.get("home"),
            "away_team": h.get("away_team") or h.get("away"),
            "score":     h.get("score") or h.get("final_score"),
        })
    if len(items) == 1:
        text = ("El historial entre estos equipos es muy reducido: solo un "
                "enfrentamiento reciente disponible. La muestra es baja, por "
                "lo que el H2H aporta contexto pero no debe usarse como "
                "fuente principal.")
    elif len(items) < 4:
        text = (f"Solo se registran {len(items)} enfrentamientos directos "
                "recientes — muestra limitada, contexto pero no fuente "
                "primaria.")
    else:
        text = (f"Se identifican {len(items)} enfrentamientos directos "
                "recientes que aportan contexto sobre el estilo del cruce.")
    return {
        "available":     True,
        "status":        "OK",
        "title":         "Enfrentamientos directos",
        "text":          text,
        "matches_found": len(items),
        "items":         items,
        "reason_codes":  ["H2H_SECTION_GENERATED"],
    }


# ─────────────────────────────────────────────────────────────────────
# 5. PROBABLE SCORE
# ─────────────────────────────────────────────────────────────────────
def _build_probable_score(match: dict,
                           goals_side: Optional[str],
                           corners_side: Optional[str]) -> dict:
    home_t = match.get("home_team") if isinstance(match.get("home_team"), dict) else {}
    away_t = match.get("away_team") if isinstance(match.get("away_team"), dict) else {}
    home_xg = _safe(match.get("home_xg") or home_t.get("xg_avg") or home_t.get("xg"))
    away_xg = _safe(match.get("away_xg") or away_t.get("xg_avg") or away_t.get("xg"))
    profile_hint = _profile_hint_for_score(goals_side, match)

    try:
        from services.football_dixon_coles import compute_scoreline_grid
        grid = compute_scoreline_grid(home_xg, away_xg, profile_hint=profile_hint)
    except Exception as exc:  # noqa: BLE001
        log.debug("[F66_PROBABLE_SCORE] grid failed: %s", exc)
        grid = {"available": False, "method": "UNAVAILABLE"}

    if not grid.get("available"):
        return {
            "available":     False,
            "status":        "MISSING",
            "title":         "Resultado probable",
            "method":        "UNAVAILABLE",
            "score":         None,
            "home_goals":    None,
            "away_goals":    None,
            "confidence":    0,
            "text":          ("No hay xG suficientes para estimar el "
                              "marcador más probable."),
            "reason_codes":  ["PROBABLE_SCORE_INSUFFICIENT_DATA"],
        }

    top = grid.get("most_likely") or {}
    score = top.get("score")
    text = _probable_score_narrative(match, score, top.get("home_goals"),
                                      top.get("away_goals"),
                                      grid.get("method"))
    return {
        "available":      True,
        "status":         "OK",
        "title":          "Resultado probable",
        "method":         grid.get("method"),
        "score":          score,
        "home_goals":     top.get("home_goals"),
        "away_goals":     top.get("away_goals"),
        "confidence":     grid.get("confidence", 0),
        "top_scorelines": grid.get("top_scorelines") or [],
        "text":           text,
        # Phase F67 — explicit guardrail: the probable_score is a CONTEXTUAL
        # hint about the most likely scoreline. It MUST NOT be used as the
        # recommended pick. The pick lives on best_protected_market + the
        # corners/goals sub-sections, which respect fragility, edge,
        # protected-market discovery and layer-conflict rules. The frontend
        # uses this flag to render an explicit "informativo, no es pick"
        # label next to the scoreline.
        "is_contextual_only": True,
        "context_disclaimer": ("Marcador informativo — NO es el mercado "
                               "recomendado. Para apostar, usa la sección "
                               "de mercados protegidos arriba."),
        "reason_codes":   [
            f"PROBABLE_SCORE_{grid.get('method')}_USED",
            "PROBABLE_SCORE_IS_CONTEXTUAL_ONLY",
        ],
    }


def _profile_hint_for_score(goals_side: Optional[str], match: dict) -> str:
    if goals_side == "UNDER":
        return "UNDER"
    if goals_side == "OVER":
        return "OVER"
    if goals_side == "BTTS_YES":
        return "BTTS"
    return "NEUTRAL"


def _probable_score_narrative(match, score, h, a, method) -> str:
    if not score:
        return "El modelo no logró estimar un marcador concreto con la información disponible."
    home_name = _team_name(match, "home")
    away_name = _team_name(match, "away")
    method_es = {
        "DIXON_COLES": "Dixon-Coles",
        "POISSON":     "Poisson",
        "HEURISTIC_BY_PROFILE": "perfil de partido",
    }.get(method, "el modelo")
    if h == a:
        verdict = "un partido equilibrado y de bajo riesgo."
    elif (h or 0) > (a or 0):
        verdict = f"a {home_name} controlando el ritmo sin proyección clara de goleada."
    else:
        verdict = f"a {away_name} con mejor lectura del partido."
    return (f"El marcador más probable según {method_es} es {score}, "
            f"con {verdict}")


# ─────────────────────────────────────────────────────────────────────
# Public entry-point
# ─────────────────────────────────────────────────────────────────────
def generate_football_editorial_prediction(
    match_payload: Any,
    *,
    odds: Optional[dict] = None,
    h2h_matches: Optional[list[dict]] = None,
) -> dict:
    """Generate the full editorial dict for a football match.

    Parameters
    ----------
    match_payload
        The pick / match dict. Expected fields (all optional, fail-soft):
        ``home_team``/``away_team`` (dict or str), ``home_corners_for_l5/l15``,
        ``home_corners_against_l5/l15`` (and ``away_*``), ``home_xg``,
        ``away_xg``, ``home_team.goals_scored_l5``, ``home_team.btts_rate_l15``,
        ``home_team.clean_sheet_rate_l15``, etc.
    odds
        Output of :func:`services.thestatsapi_client.extract_normalised_markets`,
        or ``None``. Optional.
    h2h_matches
        Up to 5 recent head-to-head match dicts. Optional.

    Returns
    -------
    A dict shaped per the F66 spec (see module docstring). Always safe to
    JSON-serialise. Never raises.
    """
    if not isinstance(match_payload, dict):
        return _empty_response(reason="MATCH_PAYLOAD_INVALID")

    # Phase F67 — pre-compute data completeness so every sub-builder can
    # adopt cautious language when the report rests on a thin payload.
    completeness_audit = _data_completeness(match_payload)
    if h2h_matches:
        completeness_audit["has_h2h"] = True
    elif h2h_matches is None:
        # Phase F67 — h2h_matches was NOT explicitly passed; the caller
        # (e.g. compute_structural_value_review) wants the engine to
        # remain synchronous. Sub-section will report INSUFFICIENT_SAMPLE.
        pass

    corners = _build_corners_prediction(match_payload, odds,
                                         completeness=completeness_audit["completeness"],
                                         available_sources=completeness_audit["available_sources"])
    goals   = _build_goals_prediction(match_payload, odds,
                                       completeness=completeness_audit["completeness"],
                                       available_sources=completeness_audit["available_sources"])
    trends  = _build_key_trends(match_payload,
                                 recommended_side_corners=corners.get("side"),
                                 recommended_side_goals=goals.get("side"))
    h2h     = _build_head_to_head(match_payload, h2h_matches)
    score   = _build_probable_score(match_payload,
                                     goals_side=goals.get("side"),
                                     corners_side=corners.get("side"))

    # Best protected market = the higher-confidence between corners & goals
    # when both are OK.
    best = _pick_best_protected_market(corners, goals)

    overall = _build_overall_narrative(match_payload, corners, goals, score, best)

    reasons: list[str] = ["INTERNAL_EDITORIAL_ANALYSIS_USED"]
    for sect in (corners, goals, trends, h2h, score):
        for code in sect.get("reason_codes", []) or []:
            if code not in reasons:
                reasons.append(code)

    return {
        "available":             True,
        "engine_version":        ENGINE_VERSION,
        "source":                SOURCE_TAG,
        "scores24_replacement":  True,
        # Phase F67 — audit of which data sources fed the report. Surfaced
        # to the UI so the user can see at a glance how thin / thick the
        # underlying payload was.
        "data_completeness":     completeness_audit,
        "editorial_sections": {
            "corners_prediction": corners,
            "goals_prediction":   goals,
            "key_trends":         trends,
            "head_to_head":       h2h,
            "probable_score":     score,
        },
        "best_protected_market": best,
        "overall_narrative_es":  overall,
        "reason_codes":          reasons,
    }


def _pick_best_protected_market(corners: dict, goals: dict) -> Optional[dict]:
    candidates = []
    for sec in (corners, goals):
        if sec.get("status") == "OK" and sec.get("recommended_market"):
            candidates.append({
                "market":      sec["recommended_market"],
                "confidence":  sec.get("confidence") or 0,
                "fragility":   max(0, 60 - (sec.get("confidence") or 0)),
                "side":        sec.get("side"),
                "market_type": sec.get("market_type"),
                "odds":        sec.get("odds"),
            })
    if not candidates:
        return None
    return max(candidates, key=lambda c: c["confidence"])


def _build_overall_narrative(match, corners, goals, score, best) -> str:
    home = _team_name(match, "home")
    away = _team_name(match, "away")
    if best:
        return (f"{home} y {away} dejan un perfil que el modelo lee mejor "
                f"hacia mercados protegidos. La apuesta destacada es "
                f"\"{best['market']}\" (confianza {best['confidence']}/100), "
                "antes que mercados de alto riesgo.")
    return (f"El cruce {home} vs {away} no presenta un mercado claramente "
            "protegido. El modelo recomienda observación antes que apuesta.")


def _empty_response(*, reason: str) -> dict:
    return {
        "available":             False,
        "engine_version":        ENGINE_VERSION,
        "source":                SOURCE_TAG,
        "scores24_replacement":  True,
        "editorial_sections":    {
            "corners_prediction": {"available": False, "status": "MISSING"},
            "goals_prediction":   {"available": False, "status": "MISSING"},
            "key_trends":         {"available": False, "status": "MISSING", "items": []},
            "head_to_head":       {"available": False, "status": "MISSING"},
            "probable_score":     {"available": False, "status": "MISSING"},
        },
        "best_protected_market": None,
        "overall_narrative_es":  "No se pudo generar el análisis editorial: payload inválido.",
        "reason_codes":          ["INTERNAL_EDITORIAL_UNAVAILABLE", reason],
    }


__all__ = [
    "ENGINE_VERSION",
    "generate_football_editorial_prediction",
]
