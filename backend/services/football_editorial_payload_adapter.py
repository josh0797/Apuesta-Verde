"""Phase F74-post — Football Editorial Payload Adapter.

Aplana datos desde múltiples ubicaciones del documento ``match`` hacia los
campos planos que espera ``generate_football_editorial_prediction``.

Fuentes leídas
==============
  * ``home_team.context.recent_fixtures``    — recientes de cada equipo
  * ``away_team.context.recent_fixtures``
  * ``home_team.context.seasonal_form``      — perfil agregado de temporada
  * ``away_team.context.seasonal_form``
  * ``live_stats``                           — live snapshot
  * ``_thestatsapi_enrichment``              — pre-match TheStatsAPI
  * ``thestatsapi_snapshot``                 — live TheStatsAPI
  * ``football_data_enrichment``             — schema F74 canónico (preferido)
  * ``h2h_recent``                           — head-to-head reciente
  * ``odds_snapshots``                       — odds disponibles

Reglas
======
  * **No muta** el match original — devuelve una **copia superficial** con
    los campos planos añadidos.
  * Cada extracción exitosa añade un reason_code a
    ``editorial_payload["internal_analysis_debug"]["reason_codes"]``.
  * Cuando hay forma reciente (recent_fixtures con ≥2 señales) pero el
    editorial seguía marcándolo como THIN, el adapter UPGRADEA
    ``data_quality`` por su cuenta y reporta
    ``EDITORIAL_DATA_QUALITY_UPGRADED_FROM_THIN``.

Output extra para UI debug
==========================
::

    editorial_payload["internal_analysis_debug"] = {
        "recent_fixtures_found":     bool,
        "recent_fixtures_flattened": bool,
        "thestatsapi_found":         bool,
        "h2h_found":                 bool,
        "market_identity_found":     bool,
        "data_quality":              "THIN|LIMITED|USABLE|STRONG",
        "missing":                   [str, ...],
        "reason_codes":              [str, ...],
    }
"""
from __future__ import annotations

import logging
from typing import Any, Optional

log = logging.getLogger(__name__)

# Reason codes
RC_ADAPTER_USED                 = "EDITORIAL_PAYLOAD_ADAPTER_USED"
RC_RECENT_FIXTURES_FLATTENED    = "RECENT_FIXTURES_CONTEXT_FLATTENED"
RC_SEASONAL_FORM_FLATTENED      = "SEASONAL_FORM_CONTEXT_FLATTENED"
RC_THESTATSAPI_FLATTENED        = "THESTATSAPI_ENRICHMENT_FLATTENED"
RC_FOOTBALL_DATA_ENRICHMENT_USED = "FOOTBALL_DATA_ENRICHMENT_USED"
RC_LIVE_STATS_FLATTENED         = "LIVE_STATS_FLATTENED"
RC_H2H_PASSED_THROUGH           = "H2H_RECENT_PASSED_THROUGH"
RC_DATA_QUALITY_UPGRADED        = "EDITORIAL_DATA_QUALITY_UPGRADED_FROM_THIN"
RC_NO_SIGNALS                   = "EDITORIAL_ADAPTER_NO_SIGNALS_FOUND"


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


def _team_name(side: Optional[dict]) -> Optional[str]:
    if not isinstance(side, dict):
        return None
    return side.get("name") or side.get("team") or side.get("team_name")


def _slice_avg(values: list, k: int) -> Optional[float]:
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


def _context_block(side: Optional[dict]) -> dict:
    if not isinstance(side, dict):
        return {}
    ctx = side.get("context")
    return ctx if isinstance(ctx, dict) else {}


def _recent_fixtures(side: Optional[dict]) -> dict:
    ctx = _context_block(side)
    rf = ctx.get("recent_fixtures") or ctx.get("last_matches")
    return rf if isinstance(rf, dict) else (rf if isinstance(rf, list) else {})


def _hgp(side: Optional[dict]) -> dict:
    """historical_goal_profile dentro de recent_fixtures."""
    rf = _recent_fixtures(side)
    if isinstance(rf, dict):
        hgp = rf.get("historical_goal_profile")
        if isinstance(hgp, dict):
            return hgp
    return {}


def _seasonal_form(side: Optional[dict]) -> dict:
    ctx = _context_block(side)
    sf = ctx.get("seasonal_form")
    return sf if isinstance(sf, dict) else {}


def _flatten_recent_for_side(side: Optional[dict], prefix: str,
                              acc: dict[str, Any],
                              signals: dict[str, bool]) -> None:
    """Aplana recent_fixtures + historical_goal_profile + seasonal_form
    a ``acc`` usando el prefix (``home_`` / ``away_``).
    """
    rf  = _recent_fixtures(side)
    hgp = _hgp(side)
    sf  = _seasonal_form(side)

    # Goals from historical_goal_profile (preferred — already aggregated).
    gf_avg = (_safe_float(hgp.get("goals_for_avg"))
              or _safe_float(hgp.get("gf_avg"))
              or _safe_float((rf or {}).get("goals_for_avg")))
    ga_avg = (_safe_float(hgp.get("goals_against_avg"))
              or _safe_float(hgp.get("ga_avg"))
              or _safe_float((rf or {}).get("goals_against_avg")))
    # Fallback: derive from arrays.
    if gf_avg is None and isinstance(rf, dict):
        gf_avg = _slice_avg(rf.get("gf") or [], 5)
    if ga_avg is None and isinstance(rf, dict):
        ga_avg = _slice_avg(rf.get("ga") or [], 5)

    if gf_avg is not None:
        acc[f"{prefix}goals_scored_l5"] = gf_avg
        signals["recent"] = True
    if ga_avg is not None:
        acc[f"{prefix}goals_allowed_l5"] = ga_avg
        signals["recent"] = True

    # BTTS / clean sheets (l15 preferido, l5 fallback).
    btts_rate = (_safe_float(hgp.get("btts_rate_l15"))
                 or _safe_float(hgp.get("btts_rate"))
                 or _safe_float(sf.get("btts_rate_l15")))
    cs_rate   = (_safe_float(hgp.get("clean_sheet_rate_l15"))
                 or _safe_float(hgp.get("clean_sheet_rate"))
                 or _safe_float(sf.get("clean_sheet_rate_l15")))
    if btts_rate is not None:
        acc[f"{prefix}btts_rate_l15"] = btts_rate
        signals["btts"] = True
    if cs_rate is not None:
        acc[f"{prefix}clean_sheet_rate_l15"] = cs_rate
        signals["cs"] = True

    # Under 2.5 rate (útil para narrativa).
    under25 = (_safe_float(hgp.get("under_2_5_rate"))
               or _safe_float(hgp.get("under_2_5_rate_l15")))
    if under25 is not None:
        acc[f"{prefix}under_2_5_rate"] = under25
        signals["under"] = True

    # Corners (cuando vienen).
    cor_for_l5  = _safe_float(hgp.get("corners_for_avg_l5") or hgp.get("corners_for_avg"))
    cor_for_l15 = _safe_float(hgp.get("corners_for_avg_l15"))
    cor_ag_l5   = _safe_float(hgp.get("corners_against_avg_l5") or hgp.get("corners_against_avg"))
    cor_ag_l15  = _safe_float(hgp.get("corners_against_avg_l15"))
    if cor_for_l5 is not None:
        acc[f"{prefix}corners_for_l5"]  = cor_for_l5
        signals["corners"] = True
    if cor_for_l15 is not None:
        acc[f"{prefix}corners_for_l15"] = cor_for_l15
    if cor_ag_l5 is not None:
        acc[f"{prefix}corners_against_l5"]  = cor_ag_l5
        signals["corners"] = True
    if cor_ag_l15 is not None:
        acc[f"{prefix}corners_against_l15"] = cor_ag_l15


def _flatten_thestatsapi(match: dict, acc: dict[str, Any],
                         signals: dict[str, bool]) -> bool:
    """Inyecta home_xg / away_xg desde TheStatsAPI / football_data_enrichment.

    Retorna True si encontró al menos un xG.
    """
    # Prefer F74 canonical payload.
    canon = match.get("football_data_enrichment") or {}
    if isinstance(canon, dict):
        xg = canon.get("xg") or {}
        if _safe_float(xg.get("home")) is not None:
            acc["home_xg"] = _safe_float(xg.get("home"))
            signals["xg"] = True
        if _safe_float(xg.get("away")) is not None:
            acc["away_xg"] = _safe_float(xg.get("away"))
            signals["xg"] = True
        team_stats = canon.get("team_stats") or {}
        if isinstance(team_stats, dict):
            for side_key, prefix in (("home", "home_"), ("away", "away_")):
                blk = team_stats.get(side_key) or {}
                if not isinstance(blk, dict):
                    continue
                if acc.get(f"{prefix}xg") is None:
                    v = _safe_float(blk.get("xg_for_avg"))
                    if v is not None:
                        acc[f"{prefix}xg"] = v
                        signals["xg"] = True
                # goals scored fallback desde team_stats.
                if acc.get(f"{prefix}goals_scored_l5") is None:
                    v = _safe_float(blk.get("goals_for_avg"))
                    if v is not None:
                        acc[f"{prefix}goals_scored_l5"] = v
                        signals["recent"] = True
                if acc.get(f"{prefix}goals_allowed_l5") is None:
                    v = _safe_float(blk.get("goals_against_avg"))
                    if v is not None:
                        acc[f"{prefix}goals_allowed_l5"] = v
                        signals["recent"] = True
    return signals.get("xg", False)


def _flatten_live_stats(match: dict, acc: dict[str, Any],
                        signals: dict[str, bool]) -> bool:
    """Best-effort live_stats flattening (cuando hay match en vivo)."""
    ls = match.get("live_stats")
    if not isinstance(ls, dict):
        return False
    used = False
    for side_key, prefix in (("home", "home_"), ("away", "away_")):
        sblk = ls.get(side_key)
        if not isinstance(sblk, dict):
            continue
        v = _safe_float(sblk.get("xg") or sblk.get("expected_goals"))
        if v is not None and acc.get(f"{prefix}xg") is None:
            acc[f"{prefix}xg"] = v
            signals["xg"] = True
            used = True
    return used


# ─────────────────────────────────────────────────────────────────────
# Public entry
# ─────────────────────────────────────────────────────────────────────
def build_editorial_ready_match_payload(match: dict) -> dict:
    """Aplana ``match`` en un payload listo para
    ``generate_football_editorial_prediction``.

    Devuelve una **copia superficial** del match enriquecida con campos
    planos (``home_xg``, ``home_goals_scored_l5``, etc.) y un sub-bloque
    ``internal_analysis_debug`` que la UI puede mostrar como collapsible.

    Nunca lanza excepciones (fail-soft).
    """
    if not isinstance(match, dict):
        return {"internal_analysis_debug": {
            "recent_fixtures_found":     False,
            "recent_fixtures_flattened": False,
            "thestatsapi_found":         False,
            "h2h_found":                 False,
            "market_identity_found":     False,
            "data_quality":              "THIN",
            "missing":                   ["match_payload_invalid"],
            "reason_codes":              [],
        }}

    out: dict[str, Any] = dict(match)  # shallow copy — no mutamos el original
    home_t = match.get("home_team") if isinstance(match.get("home_team"), dict) else {}
    away_t = match.get("away_team") if isinstance(match.get("away_team"), dict) else {}

    # ── Names + label (siempre presentes para evitar "Home/Away" genérico) ─
    teams_block = match.get("teams") if isinstance(match.get("teams"), dict) else {}
    home_name = (
        _team_name(home_t)
        or match.get("home_team_name")
        or (teams_block.get("home") or {}).get("name")
        or "Home"
    )
    away_name = (
        _team_name(away_t)
        or match.get("away_team_name")
        or (teams_block.get("away") or {}).get("name")
        or "Away"
    )
    out["home_team_name"] = home_name
    out["away_team_name"] = away_name
    out["match_label"]    = match.get("match_label") or f"{home_name} vs {away_name}"

    signals = {"recent": False, "btts": False, "cs": False, "under": False,
                "corners": False, "xg": False, "h2h": False, "market": False}
    reason_codes: list[str] = [RC_ADAPTER_USED]

    # ── 1) recent_fixtures + seasonal_form (home/away) ──────────────
    recent_home_found = bool(_recent_fixtures(home_t) or _seasonal_form(home_t))
    recent_away_found = bool(_recent_fixtures(away_t) or _seasonal_form(away_t))
    recent_found = recent_home_found or recent_away_found
    _flatten_recent_for_side(home_t, "home_", out, signals)
    _flatten_recent_for_side(away_t, "away_", out, signals)
    if recent_found:
        reason_codes.append(RC_RECENT_FIXTURES_FLATTENED)
        if _seasonal_form(home_t) or _seasonal_form(away_t):
            reason_codes.append(RC_SEASONAL_FORM_FLATTENED)

    # ── 2) TheStatsAPI / football_data_enrichment ───────────────────
    ts_used = _flatten_thestatsapi(match, out, signals)
    if isinstance(match.get("football_data_enrichment"), dict):
        reason_codes.append(RC_FOOTBALL_DATA_ENRICHMENT_USED)
    elif (isinstance(match.get("_thestatsapi_enrichment"), dict)
          or isinstance(match.get("thestatsapi_snapshot"), dict)):
        reason_codes.append(RC_THESTATSAPI_FLATTENED)

    # ── 3) live_stats fallback ──────────────────────────────────────
    if _flatten_live_stats(match, out, signals):
        reason_codes.append(RC_LIVE_STATS_FLATTENED)

    # ── 4) H2H passthrough ──────────────────────────────────────────
    h2h = match.get("h2h_recent") or match.get("h2h_matches") or match.get("h2h")
    if isinstance(h2h, list) and h2h:
        out["h2h_recent"] = h2h
        signals["h2h"] = True
        reason_codes.append(RC_H2H_PASSED_THROUGH)

    # ── 5) market_evaluated passthrough (para has_market_context) ───
    if match.get("market_evaluated") or match.get("odds"):
        signals["market"] = True

    # ── 6) data_quality computation (post-adapter) ──────────────────
    has_recent = signals["recent"]
    has_xg     = signals["xg"]
    has_btts   = signals["btts"]
    has_cs     = signals["cs"]
    has_h2h    = signals["h2h"]
    has_corners = signals["corners"]
    has_market = signals["market"]
    n_stats_sources = sum([has_recent, has_xg, has_btts, has_cs, has_corners])

    # Spec del usuario:
    # - 2 señales recientes → LIMITED
    # - recientes + xG/TheStatsAPI → USABLE
    # - recientes + xG + BTTS/CS + h2h/odds → STRONG
    if has_recent and has_xg and (has_btts or has_cs) and (has_h2h or has_market):
        data_quality = "STRONG"
    elif has_recent and has_xg:
        data_quality = "USABLE"
    elif n_stats_sources >= 2:
        data_quality = "LIMITED"
    elif n_stats_sources >= 1:
        data_quality = "LIMITED"
    else:
        data_quality = "THIN"
        reason_codes.append(RC_NO_SIGNALS)

    # Si subimos de THIN gracias al adapter, registrar upgrade.
    original_dq = (match.get("data_quality")
                   or (match.get("football_data_enrichment") or {}).get("data_quality"))
    if (original_dq == "THIN" and data_quality != "THIN"):
        reason_codes.append(RC_DATA_QUALITY_UPGRADED)

    # ── 7) Debug block for UI ───────────────────────────────────────
    missing: list[str] = []
    if not has_xg:      missing.append("xG")
    if not has_btts:    missing.append("BTTS rate L15")
    if not has_cs:      missing.append("clean sheets L15")
    if not has_corners: missing.append("corners L5/L15")
    if not has_h2h:     missing.append("h2h_recent")
    market_identity_found = isinstance(match.get("market_identity"), dict) and bool(
        (match.get("market_identity") or {}).get("identity_key")
        and not str((match.get("market_identity") or {})
                    .get("identity_key", "")).startswith("UNKNOWN:")
    )
    if not market_identity_found:
        missing.append("market_identity")

    out["internal_analysis_debug"] = {
        "recent_fixtures_found":     recent_found,
        "recent_fixtures_flattened": signals["recent"],
        "thestatsapi_found":         ts_used or isinstance(
            match.get("football_data_enrichment"), dict),
        "h2h_found":                 signals["h2h"],
        "market_identity_found":     market_identity_found,
        "data_quality":              data_quality,
        "missing":                   missing,
        "reason_codes":              reason_codes,
    }
    return out


__all__ = [
    "RC_ADAPTER_USED",
    "RC_RECENT_FIXTURES_FLATTENED",
    "RC_SEASONAL_FORM_FLATTENED",
    "RC_THESTATSAPI_FLATTENED",
    "RC_FOOTBALL_DATA_ENRICHMENT_USED",
    "RC_LIVE_STATS_FLATTENED",
    "RC_H2H_PASSED_THROUGH",
    "RC_DATA_QUALITY_UPGRADED",
    "RC_NO_SIGNALS",
    "build_editorial_ready_match_payload",
]
