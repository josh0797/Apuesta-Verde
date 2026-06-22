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
    if not has_xg:
        missing.append("xG")
    if not has_btts:
        missing.append("BTTS rate L15")
    if not has_cs:
        missing.append("clean sheets L15")
    if not has_corners:
        missing.append("corners L5/L15")
    if not has_h2h:
        missing.append("h2h_recent")
    market_identity_found = isinstance(match.get("market_identity"), dict) and bool(
        (match.get("market_identity") or {}).get("identity_key")
        and not str((match.get("market_identity") or {})
                    .get("identity_key", "")).startswith("UNKNOWN:")
    )
    if not market_identity_found:
        missing.append("market_identity")

    # Phase F82 — propagate rich h2h_context so the editorial output can
    # render concrete H2H results, not just count.
    if isinstance(match.get("h2h_context"), dict):
        out["h2h_context"] = match["h2h_context"]

    # Phase F82.1-adjust — propagate the corners_snapshot too, so the UI
    # can detect the PENDING_BACKGROUND_ENRICHMENT state and offer the
    # "Actualizar córners con 365Scores" button.
    if isinstance(match.get("corners_snapshot"), dict):
        out["corners_snapshot"] = match["corners_snapshot"]

    # Phase F83.1 — per-section availability map. The UI must consume
    # ``sections`` (NOT the legacy boolean flags below) to decide what
    # to render. This is what fixes the long-standing "xG disponible
    # vs xG faltante" contradiction in the dashboard.
    try:
        from services.football_data_availability import (
            build_data_availability_sections,
        )
        availability = build_data_availability_sections(match)
    except Exception:  # noqa: BLE001
        availability = {
            "sections":           {},
            "available_sections": [],
            "missing_sections":   list(missing),
            "missing_codes":      [],
        }

    out["data_availability"] = availability
    # Propagate at the top level too so consumers of the editorial
    # payload (UI, analyst_runs persistence) can read it without
    # opening internal_analysis_debug.
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
        # Phase F83.1 — new authoritative sections map. Old fields are
        # kept above for backwards-compat with any consumer that has not
        # migrated yet.
        "sections":                  availability["sections"],
        "available_sections":        availability["available_sections"],
        "missing_sections":          availability["missing_sections"],
        "missing_codes":             availability["missing_codes"],
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
    # Sprint-F99.3 — adapter puro F74-only (single entry point goal).
    "F99_ADAPTER_SCHEMA_VERSION",
    "RC_F99_F74_ADAPTER_USED",
    "RC_F99_LEGACY_FALLBACK_USED",
    "RC_F99_PAYLOAD_INCOMPLETE",
    "F99_FLAG_ENV_VAR",
    "is_f99_editorial_adapter_enabled",
    "build_editorial_ready_match_payload_v2",
]


# ═════════════════════════════════════════════════════════════════════
# Sprint-F99.3 · Pure F74→editorial payload adapter
# ─────────────────────────────────────────────────────────────────────
# Binding del usuario:
#   * Función pura: NO consulta db, NO ejecuta builder, NO modifica match.
#   * F74 es el contrato canónico; legacy actúa como top-up cuando F74
#     no llenó la métrica todavía.
#   * Sin odds normalizadas, sin precios, sin evaluated_market, sin
#     market_identity_key, sin edge / EV / market_trap. Solo metadato
#     descriptivo ``odds_available``.
#   * Reason codes: F99_EDITORIAL_F74_ADAPTER_USED,
#     F99_EDITORIAL_LEGACY_FALLBACK_USED, F99_EDITORIAL_PAYLOAD_INCOMPLETE.
#   * Feature flag para rollout controlado:
#     ``ENABLE_F99_EDITORIAL_F74_ADAPTER`` (env, opt-in).
# ═════════════════════════════════════════════════════════════════════

import os as _os

F99_ADAPTER_SCHEMA_VERSION = "F99-EDITORIAL-1"
F99_FLAG_ENV_VAR           = "ENABLE_F99_EDITORIAL_F74_ADAPTER"

RC_F99_F74_ADAPTER_USED     = "F99_EDITORIAL_F74_ADAPTER_USED"
RC_F99_LEGACY_FALLBACK_USED = "F99_EDITORIAL_LEGACY_FALLBACK_USED"
RC_F99_PAYLOAD_INCOMPLETE   = "F99_EDITORIAL_PAYLOAD_INCOMPLETE"

# Forbidden keys — NEVER emitted in the F99 editorial payload.
_F99_FORBIDDEN_KEYS = frozenset({
    "odds", "odds_decimal",
    "evaluated_market", "market_identity_key", "market_evaluated",
    "edge", "ev", "expected_value",
    "market_trap", "market_trap_score",
    "implied_probability", "estimated_probability",
})

# Whitelisted "side" metrics projected from F74.
_F99_SIDE_METRICS = (
    "goals_scored_l5",  "goals_scored_l15",
    "goals_conceded_l5", "goals_conceded_l15",
    "xg_for_l5",        "xg_for_l15",
    "xg_against_l5",    "xg_against_l15",
    "shots_for_l5",
    "shots_on_target_l5",
    "possession_avg_l5",
    "passes_completed_l5",
    "pass_accuracy_l5",
    "corners_for_l5",   "corners_against_l5",  "corners_total_l5",
    "corners_for_l15",  "corners_against_l15", "corners_total_l15",
    "btts_rate_l5",     "btts_rate_l15",
    "clean_sheets_l5",  "clean_sheets_l15",
    "under_2_5_rate_l5",  "under_2_5_rate_l15",
    "under_3_5_rate_l5",  "under_3_5_rate_l15",
    "form_string_l5",   "form_string_l15",
    "recent_fixtures",
    "cards_for_l5",     "cards_against_l5",
)

_F99_H2H_KEYS = ("matches", "home_wins", "away_wins", "draws", "sample")


def is_f99_editorial_adapter_enabled() -> bool:
    """Strict opt-in feature flag for the F99.3 pure editorial adapter."""
    raw = _os.environ.get(F99_FLAG_ENV_VAR, "")
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def _f99_team_name(match: dict, side: str) -> str:
    if not isinstance(match, dict):
        return ""
    block = match.get(f"{side}_team")
    if isinstance(block, dict):
        n = block.get("name")
        if n:
            return str(n)
    if isinstance(block, str):
        return block
    flat = match.get(f"{side}_team_name")
    return str(flat) if flat else ""


def _f99_is_valid_f74(enrichment: Any) -> bool:
    if not isinstance(enrichment, dict) or not enrichment:
        return False
    if enrichment.get("available"):
        return True
    home = enrichment.get("home")
    away = enrichment.get("away")
    return (isinstance(home, dict) and bool(home)) or (isinstance(away, dict) and bool(away))


def _f99_project_side(f74_side: Any) -> dict:
    if not isinstance(f74_side, dict):
        return {}
    out: dict[str, Any] = {}
    for key in _F99_SIDE_METRICS:
        v = f74_side.get(key)
        if v is None:
            continue
        if key in _F99_FORBIDDEN_KEYS:
            continue
        out[key] = v
    return out


def _f99_project_h2h(f74_h2h: Any) -> dict:
    if not isinstance(f74_h2h, dict):
        return {}
    out: dict[str, Any] = {}
    for k in _F99_H2H_KEYS:
        if k in f74_h2h and f74_h2h[k] is not None:
            out[k] = f74_h2h[k]
    return out


def _f99_strip_forbidden(d: dict) -> dict:
    if not isinstance(d, dict):
        return {}
    return {k: v for k, v in d.items() if k not in _F99_FORBIDDEN_KEYS}


def _f99_legacy_side(match: dict, side: str) -> dict:
    """Read legacy flat keys + team-block keys as a top-up only.

    Maps legacy field names to F74 canonical names so consumers don't
    need to branch. NEVER includes odds / evaluated_market / edge.
    """
    if not isinstance(match, dict):
        return {}
    team = match.get(f"{side}_team")
    team = team if isinstance(team, dict) else {}

    def _flat(suffix: str):
        return match.get(f"{side}_{suffix}")

    candidates = {
        "goals_scored_l5":      team.get("goals_scored_l5")  or _flat("goals_scored_l5"),
        "goals_scored_l15":     team.get("goals_scored_l15") or _flat("goals_scored_l15"),
        "goals_conceded_l5":    team.get("goals_conceded_l5"),
        "goals_conceded_l15":   team.get("goals_conceded_l15"),
        "xg_for_l5":            team.get("xg_for_l5") or _flat("xg"),
        "xg_against_l5":        team.get("xg_against_l5"),
        "btts_rate_l15":        team.get("btts_rate_l15"),
        "clean_sheets_l15":     team.get("clean_sheet_rate_l15"),
        "corners_for_l5":       _flat("corners_for_l5"),
        "corners_against_l5":   _flat("corners_against_l5"),
        "form_string_l5":       team.get("form_string_l5"),
    }
    return {k: v for k, v in candidates.items() if v is not None}


def _f99_odds_present(match: dict) -> bool:
    """Descriptive flag — does the match doc carry any odds signal?

    We DO NOT inspect the values; we only return True/False. Caller may
    use this for debug/telemetry but it MUST NOT alter ``data_quality``.
    """
    if not isinstance(match, dict):
        return False
    o = match.get("odds")
    if isinstance(o, dict) and o:
        return True
    try:
        if float(o) > 0:
            return True
    except (TypeError, ValueError):
        pass
    me = match.get("market_evaluated")
    if isinstance(me, str) and me.strip():
        return True
    return False


def _f99_compute_dq(home: dict, away: dict, h2h: dict) -> str:
    """Same 4-tier rules as the legacy editorial — but odds-independent."""
    def _has(d: Any, k: str) -> bool:
        if not isinstance(d, dict):
            return False
        v = d.get(k)
        if v is None:
            return False
        try:
            float(v)
            return True
        except (TypeError, ValueError):
            return isinstance(v, (list, str)) and bool(v)
    has_corners = (_has(home, "corners_for_l5") and _has(away, "corners_for_l5"))
    has_goals   = (_has(home, "goals_scored_l5") or _has(away, "goals_scored_l5")
                    or _has(home, "goals_scored_l15") or _has(away, "goals_scored_l15"))
    has_xg      = (_has(home, "xg_for_l5") and _has(away, "xg_for_l5"))
    has_btts    = (_has(home, "btts_rate_l15") or _has(away, "btts_rate_l15")
                    or _has(home, "btts_rate_l5") or _has(away, "btts_rate_l5"))
    has_cs      = (_has(home, "clean_sheets_l15") or _has(away, "clean_sheets_l15")
                    or _has(home, "clean_sheets_l5") or _has(away, "clean_sheets_l5"))
    stats = sum([has_corners, has_goals, has_xg, has_btts, has_cs])
    if stats >= 4 and (has_xg or has_btts):
        return "STRONG"
    if stats >= 2 and has_goals:
        return "USABLE"
    if stats >= 1:
        return "LIMITED"
    return "THIN"


def _f99_available_sources(home: dict, away: dict, h2h: dict) -> list[str]:
    out: list[str] = []
    def _has(d: Any, k: str) -> bool:
        if not isinstance(d, dict):
            return False
        v = d.get(k)
        return v is not None and v != ""
    if _has(home, "corners_for_l5") and _has(away, "corners_for_l5"):
        out.append("corners L5/L15")
    if (_has(home, "goals_scored_l5") or _has(home, "goals_scored_l15")
            or _has(away, "goals_scored_l5") or _has(away, "goals_scored_l15")):
        out.append("historial de goles L5/L15")
    if _has(home, "xg_for_l5") and _has(away, "xg_for_l5"):
        out.append("xG / xGA")
    if (_has(home, "btts_rate_l15") or _has(away, "btts_rate_l15")
            or _has(home, "btts_rate_l5") or _has(away, "btts_rate_l5")):
        out.append("BTTS rate L15")
    if (_has(home, "clean_sheets_l15") or _has(away, "clean_sheets_l15")
            or _has(home, "clean_sheets_l5") or _has(away, "clean_sheets_l5")):
        out.append("porterías a cero L15")
    if isinstance(h2h, dict) and (h2h.get("sample") or h2h.get("matches")):
        out.append("H2H")
    return out


def build_editorial_ready_match_payload_v2(
    match: Any,
    enrichment: Optional[dict] = None,
) -> dict:
    """Build the F99.3 editorial-ready payload (pure, F74-first).

    This is the **single editorial entry point** the user wants
    everything to migrate to. The function:

    * NEVER mutates ``match``.
    * NEVER consults ``db`` or runs the F74 builder.
    * NEVER emits odds, evaluated_market, edge or market_identity.

    Behaviour
    ---------
    1. If ``enrichment`` (or ``match["football_data_enrichment"]``) is a
       valid F74 block → use it as the primary source and stamp
       ``F99_EDITORIAL_F74_ADAPTER_USED``.
    2. Top-up missing keys from legacy locations
       (``home_team[...]`` / flat ``match[...]``) and stamp
       ``F99_EDITORIAL_LEGACY_FALLBACK_USED`` when at least one key was
       sourced from legacy.
    3. If neither F74 nor legacy yielded a usable payload, stamp
       ``F99_EDITORIAL_PAYLOAD_INCOMPLETE``.

    ``data_quality`` is recomputed strictly from football signals
    (odds-independent), respecting binding guard #9.
    """
    if not isinstance(match, dict):
        return {
            "schema_version":   F99_ADAPTER_SCHEMA_VERSION,
            "teams":            {"home": {"name": ""}, "away": {"name": ""}},
            "home":             {},
            "away":             {},
            "h2h":              {},
            "official_friendly_split": {},
            "data_quality":     "THIN",
            "available_sources": [],
            "field_provenance":  {},
            "schema_migration":  None,
            "reason_codes":     [RC_F99_PAYLOAD_INCOMPLETE],
            "_meta": {
                "adapter_path_used": "F99_NONE",
                "f74_present":       False,
                "odds_available":    False,
            },
        }

    f74 = enrichment if isinstance(enrichment, dict) else match.get("football_data_enrichment")
    use_f74 = _f99_is_valid_f74(f74)

    home_name = _f99_team_name(match, "home")
    away_name = _f99_team_name(match, "away")

    home: dict = {}
    away: dict = {}
    h2h:  dict = {}
    field_provenance: dict = {}
    schema_migration: Optional[dict] = None
    reason_codes: list[str] = []

    if use_f74:
        f74_home = f74.get("home") if isinstance(f74.get("home"), dict) else {}
        f74_away = f74.get("away") if isinstance(f74.get("away"), dict) else {}
        f74_h2h  = f74.get("h2h")  if isinstance(f74.get("h2h"),  dict) else {}
        home = _f99_project_side(f74_home)
        away = _f99_project_side(f74_away)
        h2h  = _f99_project_h2h(f74_h2h)
        field_provenance = dict(f74.get("field_provenance") or {})
        schema_migration = f74.get("schema_migration")
        reason_codes.append(RC_F99_F74_ADAPTER_USED)

    # Legacy top-up — never overrides F74 picks.
    legacy_used = False
    legacy_home = _f99_legacy_side(match, "home")
    legacy_away = _f99_legacy_side(match, "away")
    for k, v in legacy_home.items():
        if home.get(k) is None and v is not None:
            home[k] = v
            legacy_used = True
    for k, v in legacy_away.items():
        if away.get(k) is None and v is not None:
            away[k] = v
            legacy_used = True

    if legacy_used:
        reason_codes.append(RC_F99_LEGACY_FALLBACK_USED)

    # Defensive strip of forbidden keys (belt + braces).
    home = _f99_strip_forbidden(home)
    away = _f99_strip_forbidden(away)
    h2h  = _f99_strip_forbidden(h2h)

    if not home and not away and not h2h:
        reason_codes.append(RC_F99_PAYLOAD_INCOMPLETE)

    data_quality      = _f99_compute_dq(home, away, h2h)
    available_sources = _f99_available_sources(home, away, h2h)

    return {
        "schema_version":   F99_ADAPTER_SCHEMA_VERSION,
        "teams": {
            "home": {"name": home_name},
            "away": {"name": away_name},
        },
        "home":             home,
        "away":             away,
        "h2h":              h2h,
        "official_friendly_split": dict((f74 or {}).get("official_friendly_split") or {}) if use_f74 else {},
        "data_quality":     data_quality,
        "available_sources": available_sources,
        "field_provenance":  field_provenance,
        "schema_migration":  schema_migration,
        "reason_codes":     reason_codes,
        "_meta": {
            "adapter_path_used": (
                "F99_F74"   if use_f74 else
                "F99_LEGACY" if legacy_used else
                "F99_NONE"
            ),
            "f74_present":     use_f74,
            "odds_available":  _f99_odds_present(match),  # descriptivo solo
        },
    }

