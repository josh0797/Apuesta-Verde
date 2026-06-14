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
    """Phase F67 (extended by F69) — audit data sources and compute
    ``data_quality`` (THIN / LIMITED / USABLE / STRONG).

    The 4-tier classification replaces F67's 3-tier ``completeness``
    while preserving backwards-compat (``completeness`` still emitted,
    mapped to FULL/PARTIAL/THIN).

    Quality rules (F69):
      - **STRONG**: L5/L15 + xG + (BTTS or clean sheets) + (H2H or odds).
      - **USABLE**: L5/L15 + at least one of {xG, BTTS, clean_sheets}
        AND a market_evaluated/odds present (caller can override).
      - **LIMITED**: only one or two partial sources.
      - **THIN**: no L5/L15 stats, no xG, no BTTS, no clean sheets.
    """
    if not isinstance(match, dict):
        return {
            "has_corners_l5":     False,
            "has_goals_history":  False,
            "has_xg":             False,
            "has_btts_rate":      False,
            "has_clean_sheets":   False,
            "has_h2h":            False,
            "has_market_context": False,
            "completeness":       "THIN",
            "data_quality":       "THIN",
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
    # Phase F69 — market context is the bundle of (odds, edge, prob_estim,
    # prob_implied, market_evaluated) coming from the discard entry. We
    # surface it as its own source so editorials can lean on it when
    # narrative-grade stats are missing.
    has_market_context = bool(
        _safe(match.get("odds")) is not None
        or _safe(match.get("estimated_probability")) is not None
        or _safe(match.get("implied_probability")) is not None
        or (isinstance(match.get("market_evaluated"), str)
            and match.get("market_evaluated").strip())
    )

    sources = []
    if has_corners_l5:
        sources.append("corners L5/L15")
    if has_goals_history:
        sources.append("historial de goles L5/L15")
    if has_xg:
        sources.append("xG / xGA")
    if has_btts:
        sources.append("BTTS rate L15")
    if has_clean_sheets:
        sources.append("porterías a cero L15")
    if has_market_context:
        sources.append("contexto de mercado (cuota/edge)")

    stats_sources = sum([has_corners_l5, has_goals_history, has_xg,
                          has_btts, has_clean_sheets])
    # 3-tier (legacy F67) — kept for backwards compatibility.
    if stats_sources >= 4:
        completeness = "FULL"
    elif stats_sources >= 2:
        completeness = "PARTIAL"
    else:
        completeness = "THIN"

    # 4-tier (F69) — data_quality drives gating decisions in the engine.
    # NOTE: market_context (odds/edge/implied) does NOT count toward
    # data_quality because that signal is surfaced separately via
    # ``discard_reason_narrative``. data_quality strictly reflects the
    # coverage of stats sources that feed corners/goals/score narratives.
    if stats_sources >= 4 and (has_xg or has_btts):
        data_quality = "STRONG"
    elif stats_sources >= 2 and has_goals_history:
        data_quality = "USABLE"
    elif stats_sources >= 1:
        data_quality = "LIMITED"
    else:
        data_quality = "THIN"

    return {
        "has_corners_l5":     has_corners_l5,
        "has_goals_history":  has_goals_history,
        "has_xg":             has_xg,
        "has_btts_rate":      has_btts,
        "has_clean_sheets":   has_clean_sheets,
        "has_h2h":            False,        # injected externally by caller
        "has_market_context": has_market_context,
        "completeness":       completeness,
        "data_quality":       data_quality,
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
    """Phase F69 — Resolve the public name of a team for narrative text.

    Resolution order:
      1. ``match.home_team.name`` / ``match.away_team.name`` (canonical hydrated form).
      2. Plain ``match.home_team`` / ``match.away_team`` strings.
      3. ``match.home_team_name`` / ``match.away_team_name`` flat fields.
      4. Parse ``match.match_label`` (e.g. ``"Qatar vs Switzerland"``).
      5. Spanish fallback ``"equipo local"`` / ``"equipo visitante"``.

    The previous behaviour returned the English placeholders ``"Home"`` /
    ``"Away"`` which leaked into user-facing narratives. F69 forbids that.
    """
    if not isinstance(match, dict):
        return "equipo local" if side == "home" else "equipo visitante"

    key = "home_team" if side == "home" else "away_team"
    val = match.get(key) or match.get(side)
    if isinstance(val, dict):
        name = val.get("name") or val.get("label")
        if name:
            return str(name)
    elif isinstance(val, str) and val.strip():
        return val.strip()

    flat_key = "home_team_name" if side == "home" else "away_team_name"
    flat = match.get(flat_key)
    if isinstance(flat, str) and flat.strip():
        return flat.strip()

    # Parse match_label like "Qatar vs Switzerland" or "Qatar - Switzerland".
    label = match.get("match_label")
    if isinstance(label, str) and label.strip():
        parsed = _parse_match_label(label)
        if parsed:
            home_n, away_n = parsed
            return home_n if side == "home" else away_n

    return "equipo local" if side == "home" else "equipo visitante"


def _parse_match_label(label: str) -> Optional[tuple]:
    """Parse strings like ``"Qatar vs Switzerland"`` → ``("Qatar", "Switzerland")``.

    Recognised separators: ``" vs "``, ``" - "``, ``" – "``, ``" — "``,
    ``" v "`` and the plain ``"vs."`` token. Case-insensitive.
    """
    if not isinstance(label, str):
        return None
    txt = label.strip()
    if not txt:
        return None
    import re as _re
    for sep in (r"\s+vs\.?\s+", r"\s+v\s+", r"\s+-\s+", r"\s+\u2013\s+",
                r"\s+\u2014\s+"):
        m = _re.split(sep, txt, maxsplit=1, flags=_re.IGNORECASE)
        if len(m) == 2 and m[0].strip() and m[1].strip():
            return (m[0].strip(), m[1].strip())
    return None


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
                                available_sources: Optional[list] = None,
                                data_quality: str = "FULL") -> dict:
    home_name = _team_name(match, "home")
    away_name = _team_name(match, "away")

    # Phase F69 — gate: when data_quality is THIN, do not even attempt to
    # produce a corners narrative; honestly report missing data.
    if data_quality == "THIN":
        return {
            "available":     False,
            "status":        "MISSING",
            "title":         "Predicción sobre córners",
            "text":          ("No hay suficientes datos de córners L5/L15 "
                              "para emitir una lectura confiable."),
            "reason_codes":  [
                "CORNERS_PREDICTION_INSUFFICIENT_DATA",
                "CORNERS_PREDICTION_BLOCKED_BY_DATA_QUALITY_THIN",
            ],
        }

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
                              available_sources: Optional[list] = None,
                              data_quality: str = "FULL") -> dict:
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

    # Phase F69 — when data_quality is THIN we MUST NOT emit the WATCHLIST
    # narrative ("el cruce de perfiles…") because it was the duplicated
    # template across every match. Return an honest unavailable block.
    if data_quality == "THIN":
        return {
            "available":     False,
            "status":        "MISSING",
            "title":         "Predicción de goles Over/Under",
            "text":          ("No hay suficientes señales ofensivas / "
                              "defensivas para recomendar Over o Under."),
            "reason_codes":  [
                "GOALS_PREDICTION_INSUFFICIENT_DATA",
                "GOALS_PREDICTION_BLOCKED_BY_DATA_QUALITY_THIN",
            ],
        }

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
                           corners_side: Optional[str],
                           data_quality: str = "FULL") -> dict:
    home_t = match.get("home_team") if isinstance(match.get("home_team"), dict) else {}
    away_t = match.get("away_team") if isinstance(match.get("away_team"), dict) else {}
    home_xg = _safe(match.get("home_xg") or home_t.get("xg_avg") or home_t.get("xg"))
    away_xg = _safe(match.get("away_xg") or away_t.get("xg_avg") or away_t.get("xg"))

    # Phase F69 — Hard gate: when there is no xG AND data_quality is
    # THIN/LIMITED, we MUST NOT fabricate a scoreline via the heuristic
    # NEUTRAL ladder ("1-1") because that template is what users were
    # seeing duplicated across every match. Return an honest unavailable
    # block instead. Heuristic is only allowed when goals_side carries a
    # confident UNDER/OVER signal (data_quality >= USABLE).
    has_xg = (home_xg is not None and away_xg is not None)
    if not has_xg and data_quality in ("THIN", "LIMITED"):
        return {
            "available":     False,
            "status":        "MISSING",
            "title":         "Resultado probable",
            "method":        "UNAVAILABLE",
            "score":         None,
            "home_goals":    None,
            "away_goals":    None,
            "confidence":    0,
            "text":          "No disponible con suficiente confianza.",
            "is_contextual_only": True,
            "reason_codes":  [
                "PROBABLE_SCORE_INSUFFICIENT_DATA",
                f"PROBABLE_SCORE_BLOCKED_BY_DATA_QUALITY_{data_quality}",
            ],
        }

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
            "text":          "No disponible con suficiente confianza.",
            "is_contextual_only": True,
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
    # Phase F69 — also exposes ``data_quality`` (4-tier) used by the gating
    # logic that prevents fabricating 1-1 scorelines for THIN matches.
    completeness_audit = _data_completeness(match_payload)
    if h2h_matches:
        completeness_audit["has_h2h"] = True
    elif h2h_matches is None:
        # Phase F67 — h2h_matches was NOT explicitly passed; the caller
        # (e.g. compute_structural_value_review) wants the engine to
        # remain synchronous. Sub-section will report INSUFFICIENT_SAMPLE.
        pass
    data_quality = completeness_audit.get("data_quality", "THIN")

    corners = _build_corners_prediction(match_payload, odds,
                                         completeness=completeness_audit["completeness"],
                                         available_sources=completeness_audit["available_sources"],
                                         data_quality=data_quality)
    goals   = _build_goals_prediction(match_payload, odds,
                                       completeness=completeness_audit["completeness"],
                                       available_sources=completeness_audit["available_sources"],
                                       data_quality=data_quality)
    trends  = _build_key_trends(match_payload,
                                 recommended_side_corners=corners.get("side"),
                                 recommended_side_goals=goals.get("side"))
    h2h     = _build_head_to_head(match_payload, h2h_matches)
    score   = _build_probable_score(match_payload,
                                     goals_side=goals.get("side"),
                                     corners_side=corners.get("side"),
                                     data_quality=data_quality)

    # Best protected market = the higher-confidence between corners & goals
    # when both are OK.
    best = _pick_best_protected_market(corners, goals)

    # Phase F69 — discard_reason_narrative: cite the actual odds / implied
    # / estimated / edge / fragility from the discard entry so each match
    # carries a distinct, audit-grade explanation.
    discard_narrative = _build_discard_reason_narrative(match_payload)

    overall = _build_overall_narrative(match_payload, corners, goals, score, best)

    reasons: list[str] = ["INTERNAL_EDITORIAL_ANALYSIS_USED",
                          f"DATA_QUALITY_{data_quality}"]
    for sect in (corners, goals, trends, h2h, score):
        for code in sect.get("reason_codes", []) or []:
            if code not in reasons:
                reasons.append(code)
    if discard_narrative and discard_narrative.get("reason_codes"):
        for code in discard_narrative["reason_codes"]:
            if code not in reasons:
                reasons.append(code)

    # Phase F69 — top-level audit block consumed by the UI to decide
    # whether to render or suppress this editorial. ``is_generic_fallback``
    # is filled later by ``detect_duplicate_internal_editorials`` after
    # the agregator scans every entry in the summary.
    internal_audit = {
        "available":           data_quality != "THIN",
        "data_quality":        data_quality,
        "is_generic_fallback": False,
        "match_specific":      True,
        "reason_codes":        [],
    }

    return {
        "available":             True,
        "engine_version":        ENGINE_VERSION,
        "source":                SOURCE_TAG,
        "scores24_replacement":  True,
        # Phase F67 — audit of which data sources fed the report. Surfaced
        # to the UI so the user can see at a glance how thin / thick the
        # underlying payload was.
        "data_completeness":     completeness_audit,
        "data_quality":          data_quality,
        "internal_editorial_analysis": internal_audit,
        "editorial_sections": {
            "corners_prediction":      corners,
            "goals_prediction":        goals,
            "key_trends":              trends,
            "head_to_head":            h2h,
            "probable_score":          score,
            "discard_reason_narrative": discard_narrative,
        },
        "best_protected_market": best,
        "overall_narrative_es":  overall,
        "reason_codes":          reasons,
    }


# ─────────────────────────────────────────────────────────────────────
# Phase F69 — Discard reason narrative + anti-duplicate scan
# ─────────────────────────────────────────────────────────────────────
def _build_discard_reason_narrative(match: dict) -> Optional[dict]:
    """Compose a per-match narrative that cites the actual market trap
    signals (cuota / probabilidad implícita / probabilidad estimada /
    edge / fragilidad).

    Returns ``None`` when none of the market context fields are present.
    Otherwise emits a dict with ``title``, ``text``, ``reason_codes`` and
    the raw numbers so the UI can render a structured block.
    """
    if not isinstance(match, dict):
        return None
    odds        = _safe(match.get("odds"))
    prob_est    = _safe(match.get("estimated_probability"))
    prob_imp    = _safe(match.get("implied_probability"))
    edge        = _safe(match.get("edge"))
    fragility   = _safe(match.get("fragility_score"))
    reason      = match.get("reason") or match.get("discard_reason") or ""
    market_eval = match.get("market_evaluated") or ""

    if not any(v is not None for v in (odds, prob_est, prob_imp, edge, fragility)) \
            and not reason:
        return None

    reason_lc   = str(reason).lower()
    market_trap = ("trap" in reason_lc or "trampa" in reason_lc
                   or "engañ" in reason_lc)
    edge_insuf  = ("edge" in reason_lc and "insuf" in reason_lc) \
                  or (edge is not None and edge < 0)
    fragile     = "fragil" in reason_lc or "frágil" in reason_lc

    # Build the human-readable explanation.
    parts: list[str] = []
    home_name = _team_name(match, "home")
    away_name = _team_name(match, "away")
    label = f"{home_name} vs {away_name}"

    if market_trap:
        if odds is not None and prob_imp is not None and prob_est is not None:
            parts.append(
                f"En {label}, el mercado fue descartado por señales de "
                f"trampa: la cuota {odds:.2f} exige una probabilidad "
                f"implícita de {prob_imp*100:.1f}% (o {prob_imp:.1f}% si "
                f"el valor ya viene en porcentaje), pero el modelo solo "
                f"estima {prob_est*100:.1f}%."
            ) if prob_imp <= 1.0 else parts.append(
                f"En {label}, el mercado fue descartado por señales de "
                f"trampa: la cuota {odds:.2f} exige una probabilidad "
                f"implícita de {prob_imp:.1f}%, pero el modelo solo "
                f"estima {prob_est if prob_est > 1 else prob_est*100:.1f}%."
            )
        elif odds is not None:
            parts.append(
                f"En {label}, la cuota actual ({odds:.2f}) no ofrece "
                "margen suficiente respecto a la probabilidad real del "
                "evento."
            )
        else:
            parts.append(
                f"En {label}, el motor detectó señales de trampa en el "
                "mercado (engañoso para el apostador)."
            )
        if edge is not None:
            edge_pct = edge if abs(edge) > 1 else edge * 100
            parts.append(
                f"Edge {edge_pct:+.1f}% — aunque el partido pueda tener "
                "lectura favorable, el precio no ofrece margen suficiente."
            )
    elif edge_insuf:
        if odds is not None and edge is not None:
            edge_pct = edge if abs(edge) > 1 else edge * 100
            parts.append(
                f"En {label}, el descarte es por edge insuficiente: la "
                f"cuota {odds:.2f} y el edge estimado ({edge_pct:+.1f}%) "
                "no superan el umbral de valor del motor."
            )
    elif fragile:
        if fragility is not None:
            parts.append(
                f"En {label}, el mercado fue clasificado como frágil "
                f"(score {int(fragility)}/100): el motor prefiere no "
                "exponer capital en estas condiciones."
            )
    elif reason:
        parts.append(
            f"En {label}, el motor descartó el mercado por: "
            f"{str(reason).strip().rstrip('.')}."
        )

    if market_eval and market_eval not in ("—", "-", ""):
        parts.append(f"Mercado evaluado: {market_eval}.")

    if not parts:
        return None

    reason_codes = ["DISCARD_REASON_NARRATIVE_GENERATED"]
    if market_trap:
        reason_codes.append("MARKET_TRAP_NARRATIVE_INJECTED")
    if edge_insuf:
        reason_codes.append("EDGE_INSUFFICIENT_NARRATIVE_INJECTED")
    if fragile:
        reason_codes.append("FRAGILE_MARKET_NARRATIVE_INJECTED")

    return {
        "available":     True,
        "status":        "OK",
        "title":         "Motivo del descarte",
        "text":          " ".join(parts),
        "odds":          odds,
        "estimated_probability": prob_est,
        "implied_probability":   prob_imp,
        "edge":          edge,
        "fragility":     fragility,
        "reason":        reason,
        "market_evaluated": market_eval or None,
        "reason_codes":  reason_codes,
    }


def _normalise_text(s: str) -> str:
    """Lower + strip + collapse whitespace + remove punctuation/digits
    so two narratives that only differ in team names / numbers collapse
    to the same string. Used by the anti-duplicate scan."""
    if not isinstance(s, str):
        return ""
    import re as _re
    out = s.lower()
    # Remove digits (so "1-0 11%" doesn't differ between entries)
    out = _re.sub(r"\d+([.,]\d+)?", "#", out)
    # Strip punctuation
    out = _re.sub(r"[^\w\s#]", " ", out, flags=_re.UNICODE)
    out = _re.sub(r"\s+", " ", out).strip()
    return out


def _editorial_fingerprint(editorial: dict, *, strip_teams: bool = True) -> str:
    """Build a fingerprint from the editorial's narrative parts.

    When ``strip_teams`` is True (default), the home/away team names are
    masked out so two THIN matches differing ONLY in team names are still
    detected as duplicates.
    """
    if not isinstance(editorial, dict):
        return ""
    secs = editorial.get("editorial_sections") or {}
    pieces: list[str] = []
    for key in ("corners_prediction", "goals_prediction", "head_to_head",
                "probable_score"):
        sec = secs.get(key) or {}
        if isinstance(sec, dict) and sec.get("text"):
            pieces.append(sec["text"])
    trends = secs.get("key_trends") or {}
    if isinstance(trends.get("items"), list):
        pieces.extend(trends["items"])
    overall = editorial.get("overall_narrative_es")
    if isinstance(overall, str):
        pieces.append(overall)
    text = " || ".join(p for p in pieces if isinstance(p, str))
    if strip_teams:
        # Mask team-name tokens injected by the editorial.
        # We don't know the exact names here; rely on _normalise_text to
        # collapse them via word-boundary stripping. A second pass replaces
        # any consecutive Capitalised tokens.
        import re as _re
        text = _re.sub(r"\b[A-ZÁÉÍÓÚÑ][\wáéíóúñ]+(?:\s+[A-ZÁÉÍÓÚÑ][\wáéíóúñ]+)*",
                       "TEAM", text)
    return _normalise_text(text)


def _similarity(a: str, b: str) -> float:
    """Token-set Jaccard similarity. 1.0 == identical, 0.0 == disjoint."""
    if not a or not b:
        return 0.0
    sa = set(a.split())
    sb = set(b.split())
    if not sa or not sb:
        return 0.0
    inter = len(sa & sb)
    union = len(sa | sb)
    return inter / union if union else 0.0


def detect_duplicate_internal_editorials(summary: dict,
                                          *, threshold: float = 0.85) -> int:
    """Phase F69 — scan every entry in the summary buckets and flag any
    editorial that is >threshold similar to another match's editorial.

    Marks ``editorial_prediction.internal_editorial_analysis.is_generic_fallback``
    to True and appends the ``INTERNAL_EDITORIAL_DUPLICATE_TEMPLATE_DETECTED``
    reason code on every offending entry.

    Returns the number of entries flagged.
    """
    if not isinstance(summary, dict):
        return 0
    entries: list[dict] = []
    for bucket_key in ("discarded_market", "discarded_motivation",
                        "incomplete_data", "watchlist_odds_needed",
                        "discarded_unknown"):
        bucket = summary.get(bucket_key) or []
        if not isinstance(bucket, list):
            continue
        for e in bucket:
            if isinstance(e, dict) and isinstance(e.get("editorial_prediction"), dict):
                entries.append(e)

    if len(entries) < 2:
        return 0

    # Compute fingerprints once per entry.
    fps: list[tuple[dict, str]] = []
    for e in entries:
        ed = e["editorial_prediction"]
        fps.append((e, _editorial_fingerprint(ed, strip_teams=True)))

    flagged_idx: set[int] = set()
    for i in range(len(fps)):
        if not fps[i][1]:
            continue
        for j in range(i + 1, len(fps)):
            if not fps[j][1]:
                continue
            sim = _similarity(fps[i][1], fps[j][1])
            if sim >= threshold:
                flagged_idx.add(i)
                flagged_idx.add(j)

    count = 0
    for idx in flagged_idx:
        entry = fps[idx][0]
        ed = entry.get("editorial_prediction") or {}
        audit = ed.get("internal_editorial_analysis") or {}
        audit["is_generic_fallback"] = True
        audit["match_specific"] = False
        codes = audit.get("reason_codes") or []
        if "INTERNAL_EDITORIAL_DUPLICATE_TEMPLATE_DETECTED" not in codes:
            codes.append("INTERNAL_EDITORIAL_DUPLICATE_TEMPLATE_DETECTED")
        audit["reason_codes"] = codes
        audit["warning"] = ("Editorial interno genérico detectado; se ocultó "
                            "para evitar lectura falsa.")
        ed["internal_editorial_analysis"] = audit
        # Bubble up to the engine reason_codes so the badge/log sees it.
        top_codes = ed.get("reason_codes") or []
        if "INTERNAL_EDITORIAL_DUPLICATE_TEMPLATE_DETECTED" not in top_codes:
            top_codes.append("INTERNAL_EDITORIAL_DUPLICATE_TEMPLATE_DETECTED")
            ed["reason_codes"] = top_codes
        count += 1
    return count


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


# ─────────────────────────────────────────────────────────────────────
# Phase F86.2 — Consumer for h2h_decision + xg_recent_averages
# ─────────────────────────────────────────────────────────────────────
# The ingestor (F85+F86) attaches three new blocks on ``match_doc``:
#   * ``match_doc["h2h_context"]``   — F86 classified context.
#   * ``match_doc["h2h_decision"]``  — F86 ``points_by_market`` + signals.
#   * ``match_doc["xg_recent_averages"]`` — F85 L1/L5/L15 background job.
# This consumer surfaces them in the editorial so the UI can render
# matches one-by-one (thin sample), apply scoring deltas via
# ``football_h2h_scoring_applier`` and respect PENDING/TIMEOUT states.

_H2H_SIGNAL_TRANSLATIONS_ES = {
    "H2H_PROFILE_OVER_1_5":  "Over 1.5 goles en {pct}% de los enfrentamientos recientes",
    "H2H_PROFILE_OVER_2_5":  "Over 2.5 goles en {pct}% de los enfrentamientos recientes",
    "H2H_PROFILE_OVER_3_5":  "Over 3.5 goles en {pct}% de los enfrentamientos recientes",
    "H2H_PROFILE_UNDER_1_5": "Under 1.5 goles en {pct}% de los enfrentamientos recientes",
    "H2H_PROFILE_UNDER_2_5": "Under 2.5 goles en {pct}% de los enfrentamientos recientes",
    "H2H_PROFILE_UNDER_3_5": "Under 3.5 goles en {pct}% de los enfrentamientos recientes",
    "H2H_PROFILE_BTTS_YES":  "Ambos anotan en {pct}% de los enfrentamientos recientes",
    "H2H_PROFILE_BTTS_NO":   "Ambos NO anotan en {pct}%",
    "H2H_HOME_DOMINANT":     "{home_name} no pierde en {pct}%",
    "H2H_AWAY_DOMINANT":     "{away_name} no pierde en {pct}%",
}

_H2H_SIGNAL_TO_RATE_KEY = {
    "H2H_PROFILE_OVER_1_5":  "over_1_5",
    "H2H_PROFILE_OVER_2_5":  "over_2_5",
    "H2H_PROFILE_OVER_3_5":  "over_3_5",
    "H2H_PROFILE_UNDER_1_5": "under_1_5",
    "H2H_PROFILE_UNDER_2_5": "under_2_5",
    "H2H_PROFILE_UNDER_3_5": "under_3_5",
    "H2H_PROFILE_BTTS_YES":  "btts_yes",
    "H2H_PROFILE_BTTS_NO":   "btts_no",
    "H2H_HOME_DOMINANT":     "home_dnb",
    "H2H_AWAY_DOMINANT":     "away_dnb",
}


def _result_for_home(score_str: Any, side_of_home: Optional[str]) -> Optional[str]:
    """Return 'W' | 'D' | 'L' for the *current fixture's home team*
    based on the historical match score, given whether they played
    home or away in that match.
    """
    if not isinstance(score_str, str) or "-" not in score_str:
        return None
    try:
        h, a = score_str.split("-", 1)
        h, a = int(h.strip()), int(a.strip())
    except (TypeError, ValueError):
        return None
    if side_of_home == "home":
        if h > a:
            return "W"
        if h < a:
            return "L"
        return "D"
    if side_of_home == "away":
        if a > h:
            return "W"
        if a < h:
            return "L"
        return "D"
    return None


def _matches_detail(
    h2h_recent: Optional[list],
    home_team_name: str,
    *,
    max_days_recent: int = 365,
) -> list[dict]:
    """Project the raw H2H list into the UI-friendly detail shape.

    Each entry → {date, home, away, score, is_recent, result_for_home}.
    """
    if not isinstance(h2h_recent, list):
        return []
    try:
        from .football_h2h_decision_policy import _is_recent  # type: ignore
    except Exception:  # noqa: BLE001
        def _is_recent(s: Any, *, max_days: int = max_days_recent) -> bool:  # type: ignore
            return False

    home_lc = (home_team_name or "").strip().lower()
    out: list[dict] = []
    for m in h2h_recent:
        if not isinstance(m, dict):
            continue
        home_n = m.get("home") or m.get("home_team") or ""
        away_n = m.get("away") or m.get("away_team") or ""
        score  = m.get("score") or m.get("final_score") or ""
        side_of_home: Optional[str] = None
        if isinstance(home_n, str) and home_n.strip().lower() == home_lc:
            side_of_home = "home"
        elif isinstance(away_n, str) and away_n.strip().lower() == home_lc:
            side_of_home = "away"
        out.append({
            "date":             m.get("date") or m.get("utc_date") or "",
            "home":             home_n,
            "away":             away_n,
            "score":            score,
            "is_recent":        bool(_is_recent(m.get("date"))),
            "result_for_home":  _result_for_home(score, side_of_home),
        })
    return out


def _build_h2h_narrative(
    *,
    classified: dict,
    decision: dict,
    matches_detail: list[dict],
    home_name: str,
    away_name: str,
) -> str:
    """Compose the human-readable narrative for the H2H block."""
    sample_total  = int(classified.get("sample_size_total")  or 0)
    sample_recent = int(classified.get("sample_size_recent") or 0)
    decision_useful = bool(classified.get("decision_useful"))
    applied_signals = list((decision or {}).get("signals") or [])
    rates           = (decision or {}).get("rates") or {}

    if sample_total == 0:
        return ("Sin enfrentamientos directos previos registrados — "
                "sin contexto H2H.")

    if not decision_useful:
        # Show the most recent match factually.
        last = matches_detail[0] if matches_detail else None
        if last:
            return (
                f"{sample_total} enfrentamiento(s) directo(s) registrado(s). "
                f"Último: {last.get('date', '—')} → {last.get('home', '—')} "
                f"{last.get('score', '—')} {last.get('away', '—')}. "
                "Muestra insuficiente / antigua para influir en la "
                "decisión; se muestra como contexto."
            )
        return (
            f"{sample_total} enfrentamiento(s) directo(s) registrado(s). "
            "Muestra insuficiente para influir en la decisión; se muestra "
            "como contexto."
        )

    # decision_useful == True
    if not applied_signals:
        return (
            f"{sample_recent} enfrentamientos en últimos 12 meses, pero "
            "ningún patrón cruza los umbrales de decisión. Contexto "
            "informativo."
        )

    bullets: list[str] = []
    for sig in applied_signals:
        tmpl = _H2H_SIGNAL_TRANSLATIONS_ES.get(sig)
        if not tmpl:
            continue
        rate_key = _H2H_SIGNAL_TO_RATE_KEY.get(sig)
        rate = rates.get(rate_key) if rate_key else None
        try:
            pct = int(round(float(rate) * 100)) if rate is not None else 0
        except (TypeError, ValueError):
            pct = 0
        bullets.append(tmpl.format(
            pct=pct, home_name=home_name, away_name=away_name,
        ))
    intro = (f"{sample_recent} enfrentamientos en últimos 12 meses. "
             f"Patrón claro: ")
    if not bullets:
        return intro.rstrip(": ") + "."
    return intro + "; ".join(bullets) + "."


def _build_h2h_block(match_doc: dict) -> dict:
    """Produce the F86.2 ``h2h_block`` consumed by the UI.

    Reads ``h2h_context``, ``h2h_decision`` and ``h2h_recent`` from
    ``match_doc``. Fail-soft on every missing field.
    """
    classified = (match_doc.get("h2h_context")  or {}) if isinstance(match_doc, dict) else {}
    decision   = (match_doc.get("h2h_decision") or {}) if isinstance(match_doc, dict) else {}
    raw_recent = (match_doc.get("h2h_recent")   or []) if isinstance(match_doc, dict) else []

    # When the ingestor's h2h_recent is empty fall back to the
    # ``recent_matches`` field of the classified context (also a list).
    if not raw_recent and isinstance(classified.get("recent_matches"), list):
        raw_recent = classified["recent_matches"]

    home_name = _team_name(match_doc if isinstance(match_doc, dict) else {}, "home")
    away_name = _team_name(match_doc if isinstance(match_doc, dict) else {}, "away")

    detail = _matches_detail(raw_recent, home_name)
    decision_useful = bool(classified.get("decision_useful"))
    rates = (decision.get("rates") or {}) if decision_useful else {}
    applied_signals = list(decision.get("signals") or []) if decision.get("applied") else []
    narrative = _build_h2h_narrative(
        classified=classified, decision=decision, matches_detail=detail,
        home_name=home_name, away_name=away_name,
    )

    return {
        "available":          bool(classified) or bool(detail),
        "decision_useful":    decision_useful,
        "sample_size_total":  int(classified.get("sample_size_total")  or len(detail)),
        "sample_size_recent": int(classified.get("sample_size_recent") or 0),
        "warnings":           list(classified.get("warnings") or []),
        "matches_detail":     detail,
        "rates":              rates if isinstance(rates, dict) else {},
        "applied_signals":    applied_signals,
        "narrative":          narrative,
        "points_by_market":   dict(decision.get("points_by_market") or {}),
    }


def _build_xg_block(match_doc: dict) -> dict:
    """Produce the F86.2 ``xg_block`` consumed by the UI.

    Honours the PENDING/SUCCESS/TIMEOUT/UNAVAILABLE state stamped by
    ``_schedule_xg_recent_background`` (F87) and pulls signals from
    :func:`football_xg_signals.derive_xg_signals` when available.
    """
    xg_recent = (match_doc.get("xg_recent_averages") if isinstance(match_doc, dict) else None) or {}
    status_raw = str(xg_recent.get("status") or "").upper()

    # Normalise status into the UI vocabulary.
    if status_raw == "PENDING_BACKGROUND_ENRICHMENT":
        status = "PENDING"
    elif status_raw in ("SUCCESS", "TIMEOUT", "UNAVAILABLE"):
        status = status_raw
    elif xg_recent.get("available"):
        status = "SUCCESS"
    elif xg_recent:
        status = "UNAVAILABLE"
    else:
        status = "UNAVAILABLE"

    block: dict = {
        "status":         status,
        "partial":        bool(xg_recent.get("partial")),
        "home":           None,
        "away":           None,
        "signals":        [],
        "explanations":   {},
        "missing_reason": None,
    }

    if status == "PENDING":
        block["missing_reason"] = (
            "Cómputo de xG L1/L5/L15 en proceso — refresca en 10s."
        )
        return block

    if status in ("UNAVAILABLE", "TIMEOUT"):
        block["missing_reason"] = (
            "xG no disponible (TheStatsAPI sin shotmaps para este equipo)."
        )
        return block

    # SUCCESS path — surface L1/L5/L15 + signals.
    home_block = xg_recent.get("home") or {}
    away_block = xg_recent.get("away") or {}
    home_team_name = _team_name(match_doc if isinstance(match_doc, dict) else {}, "home")
    away_team_name = _team_name(match_doc if isinstance(match_doc, dict) else {}, "away")

    def _project_side(side_payload: dict, team_label: str) -> dict:
        proj: dict = {"team": team_label}
        for window in ("l1", "l5", "l15"):
            w = side_payload.get(window) if isinstance(side_payload, dict) else None
            if isinstance(w, dict):
                proj[window] = {
                    "xg_for":     _safe(w.get("xg_for_avg")),
                    "xg_against": _safe(w.get("xg_against_avg")),
                    "sample":     w.get("sample"),
                }
            else:
                proj[window] = None
        return proj

    block["home"] = _project_side(home_block, home_team_name)
    block["away"] = _project_side(away_block, away_team_name)

    try:
        from .football_xg_signals import derive_xg_signals
        derived = derive_xg_signals(xg_recent) or {}
        block["signals"]      = list(derived.get("signals") or [])
        block["explanations"] = dict(derived.get("explanations") or {})
    except Exception as exc:  # noqa: BLE001
        log.debug("[F86.2_XG_SIGNALS] derive failed: %s", exc)

    return block


# Patch the public entry-point to attach the new blocks and (optionally)
# bump the best_protected_market via H2H scoring deltas. We monkey-patch
# in-place to avoid duplicating the long signature.
_original_generate_football_editorial_prediction = generate_football_editorial_prediction


def generate_football_editorial_prediction(  # type: ignore[no-redef]
    match_payload: Any,
    *,
    odds: Optional[dict] = None,
    h2h_matches: Optional[list[dict]] = None,
) -> dict:
    """Phase F86.2 wrapper — extends the F66 output with ``h2h_block``
    and ``xg_block`` and applies the H2H scoring delta to the
    ``best_protected_market`` (clamped, polarity-guarded).
    """
    out = _original_generate_football_editorial_prediction(
        match_payload, odds=odds, h2h_matches=h2h_matches,
    )
    if not isinstance(out, dict):
        return out
    if not isinstance(match_payload, dict):
        return out

    # Build new blocks (fail-soft).
    try:
        out["h2h_block"] = _build_h2h_block(match_payload)
    except Exception as exc:  # noqa: BLE001
        log.warning("[F86.2_H2H_BLOCK_FAIL] %s", exc)
        out["h2h_block"] = {
            "available": False, "decision_useful": False,
            "sample_size_total": 0, "sample_size_recent": 0,
            "warnings": [], "matches_detail": [], "rates": {},
            "applied_signals": [], "narrative": "",
            "points_by_market": {},
        }
    try:
        out["xg_block"] = _build_xg_block(match_payload)
    except Exception as exc:  # noqa: BLE001
        log.warning("[F86.2_XG_BLOCK_FAIL] %s", exc)
        out["xg_block"] = {
            "status": "UNAVAILABLE", "partial": False,
            "home": None, "away": None, "signals": [],
            "explanations": {}, "missing_reason": str(exc),
        }

    # Apply H2H scoring delta on the best_protected_market (in-place).
    best = out.get("best_protected_market")
    h2h_decision = match_payload.get("h2h_decision") or {}
    if isinstance(best, dict) and h2h_decision.get("applied"):
        try:
            from .football_h2h_scoring_applier import (
                apply_h2h_points_to_candidate,
            )
            # The editorial uses ``confidence`` (not confidence_score) —
            # mirror both for downstream consumers.
            best.setdefault("confidence_score", best.get("confidence") or 0)
            result = apply_h2h_points_to_candidate(best, h2h_decision)
            if result.get("applied"):
                # Keep ``confidence`` mirrored for back-compat with the
                # legacy UI chip.
                best["confidence"] = best.get("confidence_score")
                out.setdefault("reason_codes", []).append(
                    "H2H_SCORING_APPLIED_TO_BEST_PROTECTED_MARKET"
                )
                if result.get("clamped"):
                    out["reason_codes"].append("H2H_SCORING_CLAMPED_AT_MAX_DELTA")
                if result.get("polarity_conflict"):
                    out["reason_codes"].append("H2H_SCORING_POLARITY_CONFLICT")
        except Exception as exc:  # noqa: BLE001
            log.warning("[F86.2_H2H_APPLY_FAIL] %s", exc)

    return out


__all__ = [
    "ENGINE_VERSION",
    "generate_football_editorial_prediction",
    "_build_h2h_block",
    "_build_xg_block",
]
