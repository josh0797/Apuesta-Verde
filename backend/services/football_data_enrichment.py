"""Phase F74 — Canonical Football Data Enrichment schema.

Este módulo unifica los distintos proveedores de enriquecimiento de
fútbol en un **único schema canónico**. La idea es eliminar la
fragmentación entre:

  * ``_thestatsapi_enrichment``         (TheStatsAPI pre-match)
  * ``thestatsapi_snapshot``            (TheStatsAPI live snapshot)
  * ``external_context.thestatsapi``    (capa intermedia previa)
  * API-Sports (cuando trae stats útiles)
  * Forebet (contexto externo: predicción 1X2, marcador, goles esperados)

…y exponer una vista única (``CanonicalFootballEnrichment``) lista para
ser consumida por:

  * el motor editorial interno,
  * el clasificador de tolerancia / moneyball,
  * los validadores de Forebet / OddsPortal,
  * la UI (badges de calidad de datos, edge, etc.).

Diseño
------

El normalizador es **fail-soft**: cada sección es opcional, todas las
faltas se trazan con ``reason_codes`` y nunca rompe el pipeline. La
calidad agregada se reporta con la escala estándar del proyecto:

  THIN < LIMITED < USABLE < STRONG

Reglas críticas (Phase F73/F74):
  * ``estimated_probabilities`` SOLO se llena cuando ``data_quality``
    no es ``THIN``.
  * Si el ``market_identity_key`` asociado al pick es UNKNOWN, el
    schema marca ``requires_market_identity=True`` y bloquea cualquier
    cálculo de edge / clasificación de trampa downstream.

Este módulo NO calcula probabilidades por sí mismo: la inyección la
hacen proveedores específicos como
``services/external_sources/thestatsapi_football_enrichment.py`` que
sí saben mapear ``xg``/``form`` a probabilidades por mercado vía
Dixon-Coles o Poisson.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional

log = logging.getLogger(__name__)


SCHEMA_VERSION = "F74-1"

# Escala estándar de data_quality.
DQ_THIN     = "THIN"
DQ_LIMITED  = "LIMITED"
DQ_USABLE   = "USABLE"
DQ_STRONG   = "STRONG"

_DQ_ORDER = {DQ_THIN: 0, DQ_LIMITED: 1, DQ_USABLE: 2, DQ_STRONG: 3}


# Reason codes — todos los códigos son strings para que viajen sanos en
# JSON y sean fácilmente grepables en logs/tests.
RC_NO_PROVIDERS              = "ENRICHMENT_NO_PROVIDERS"
RC_THIN_DATA                 = "ENRICHMENT_THIN_DATA"
RC_PARTIAL_DATA              = "ENRICHMENT_PARTIAL_DATA"
RC_THESTATSAPI_USED          = "ENRICHMENT_THESTATSAPI_USED"
RC_THESTATSAPI_SNAPSHOT_USED = "ENRICHMENT_THESTATSAPI_SNAPSHOT_USED"
RC_EXTERNAL_CONTEXT_USED     = "ENRICHMENT_EXTERNAL_CONTEXT_USED"
RC_APISPORTS_USED            = "ENRICHMENT_APISPORTS_USED"
RC_FOREBET_USED              = "ENRICHMENT_FOREBET_USED"
RC_XG_AVAILABLE              = "ENRICHMENT_XG_AVAILABLE"
RC_XG_MISSING                = "ENRICHMENT_XG_MISSING"
RC_REQUIRES_MARKET_IDENTITY  = "ENRICHMENT_REQUIRES_MARKET_IDENTITY"
RC_PROBABILITIES_BLOCKED_THIN = "ENRICHMENT_PROBABILITIES_BLOCKED_THIN_DATA"


# ─────────────────────────────────────────────────────────────────────
# Helpers privados
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


def _pick_first(d: Optional[dict], *keys: str) -> Any:
    if not isinstance(d, dict):
        return None
    for k in keys:
        if k in d and d[k] is not None:
            return d[k]
    return None


def _extract_team_names(match_doc: dict) -> dict:
    """Best-effort: extrae nombres canónicos home/away."""
    home_name = None
    away_name = None
    home_id   = None
    away_id   = None

    teams = match_doc.get("teams")
    if isinstance(teams, dict):
        home_name = (teams.get("home") or {}).get("name") if isinstance(teams.get("home"), dict) else None
        away_name = (teams.get("away") or {}).get("name") if isinstance(teams.get("away"), dict) else None
        home_id   = (teams.get("home") or {}).get("id")   if isinstance(teams.get("home"), dict) else None
        away_id   = (teams.get("away") or {}).get("id")   if isinstance(teams.get("away"), dict) else None

    home_name = home_name or _pick_first(match_doc, "home_team", "home_name")
    away_name = away_name or _pick_first(match_doc, "away_team", "away_name")

    return {
        "home": {"id": home_id, "name": home_name},
        "away": {"id": away_id, "name": away_name},
    }


def _extract_xg_from_thestatsapi(enrichment: dict) -> dict:
    """Intenta extraer xG (home/away) desde un payload ``_thestatsapi_enrichment``.

    El layout esperado es::

        enrichment["team_stats"] = {
            "home": {... "expected_goals_for": float ...},
            "away": {... "expected_goals_for": float ...},
        }

    Devuelve ``{"home": float|None, "away": float|None, "found": bool}``.
    """
    if not isinstance(enrichment, dict):
        return {"home": None, "away": None, "found": False}
    team_stats = enrichment.get("team_stats") or {}
    if not isinstance(team_stats, dict):
        return {"home": None, "away": None, "found": False}
    home_s = team_stats.get("home") or {}
    away_s = team_stats.get("away") or {}

    def _xg(stats: dict) -> Optional[float]:
        if not isinstance(stats, dict):
            return None
        for key in ("expected_goals_per_match", "xg_per_match", "xg",
                     "expected_goals_for", "xG", "xg_for"):
            v = _safe_float(stats.get(key))
            if v is not None:
                return v
        return None

    h = _xg(home_s)
    a = _xg(away_s)
    return {"home": h, "away": a, "found": bool(h is not None or a is not None)}


def _extract_xg_from_apisports(apisports: Any) -> dict:
    """API-Sports rara vez expone xG estable; intentamos en stats si lo trae."""
    if not isinstance(apisports, dict):
        return {"home": None, "away": None, "found": False}
    stats = apisports.get("statistics") or apisports.get("team_statistics") or {}
    home_xg = away_xg = None
    if isinstance(stats, dict):
        home_xg = _safe_float((stats.get("home") or {}).get("expected_goals"))
        away_xg = _safe_float((stats.get("away") or {}).get("expected_goals"))
    return {"home": home_xg, "away": away_xg,
            "found": bool(home_xg is not None or away_xg is not None)}


def _extract_forebet_context(match_doc: dict) -> Optional[dict]:
    """Devuelve un sub-dict con la predicción Forebet si está adjuntada."""
    forebet = (
        match_doc.get("_forebet_prediction")
        or (match_doc.get("external_context") or {}).get("forebet")
        or (match_doc.get("external_fallback") or {}).get("forebet")
    )
    if not isinstance(forebet, dict):
        return None
    out = {
        "probabilities": forebet.get("probabilities") or forebet.get("probs"),
        "predicted_score": forebet.get("predicted_score") or forebet.get("score"),
        "goals_avg": _safe_float(forebet.get("goals_avg") or forebet.get("expected_goals_total")),
        "pick": forebet.get("pick"),
        "source": "forebet",
    }
    # Drop empty keys to keep payload compact.
    return {k: v for k, v in out.items() if v not in (None, {}, [])}


def _compute_data_quality(payload_summary: dict) -> tuple[str, list[str]]:
    """Calcula calidad agregada según qué providers aportaron qué.

    Reglas (heurística estable):
      * STRONG  → xg disponible (home y away) Y al menos 2 providers.
      * USABLE  → xg disponible (al menos un lado) O ≥2 providers con team_stats.
      * LIMITED → cualquier proveedor con team_stats parciales o solo Forebet.
      * THIN    → no hay providers utilizables.
    """
    codes: list[str] = []
    n_providers = sum(
        1 for v in payload_summary["providers_used"] if v
    )
    xg_home = payload_summary["xg"]["home"]
    xg_away = payload_summary["xg"]["away"]
    has_xg_both = xg_home is not None and xg_away is not None
    has_xg_any  = xg_home is not None or xg_away is not None

    if n_providers == 0:
        codes.append(RC_NO_PROVIDERS)
        codes.append(RC_THIN_DATA)
        return DQ_THIN, codes

    if has_xg_both and n_providers >= 2:
        codes.append(RC_XG_AVAILABLE)
        return DQ_STRONG, codes
    if has_xg_both:
        codes.append(RC_XG_AVAILABLE)
        return DQ_USABLE, codes
    if has_xg_any:
        codes.append(RC_XG_AVAILABLE)
        codes.append(RC_PARTIAL_DATA)
        return DQ_USABLE, codes
    # No xG en absoluto.
    codes.append(RC_XG_MISSING)
    codes.append(RC_PARTIAL_DATA)
    return DQ_LIMITED, codes


# ─────────────────────────────────────────────────────────────────────
# Entry point principal
# ─────────────────────────────────────────────────────────────────────
def normalize_football_enrichment(
    match_doc: dict,
    *,
    market_identity: Optional[dict] = None,
    extra_sources: Optional[dict] = None,
) -> dict:
    """Devuelve el schema canónico unificado para un match de fútbol.

    Parameters
    ----------
    match_doc :
        Documento del match con cualquiera de las claves de proveedor
        soportadas (``_thestatsapi_enrichment``, ``thestatsapi_snapshot``,
        ``external_context.thestatsapi``, ``_forebet_prediction``, etc.).
    market_identity :
        (Opcional) salida de ``services.market_identity.normalize_market_identity``.
        Si se pasa y es UNKNOWN, el schema marca
        ``requires_market_identity=True`` y deja
        ``estimated_probabilities`` vacío.
    extra_sources :
        Hook opcional para inyectar fuentes adicionales (testing /
        proveedores nuevos). Cualquier key ``"thestatsapi"`` /
        ``"forebet"`` / ``"apisports"`` en este dict se mezcla por
        encima de las extraídas del match_doc.

    Returns
    -------
    dict
        Canonical Football Enrichment payload (ver docstring del módulo).
    """
    if not isinstance(match_doc, dict):
        match_doc = {}
    extra_sources = extra_sources or {}

    ts_pre   = (match_doc.get("_thestatsapi_enrichment")
                 or extra_sources.get("thestatsapi"))
    ts_snap  = (match_doc.get("thestatsapi_snapshot")
                 or extra_sources.get("thestatsapi_snapshot"))
    ext_ctx  = ((match_doc.get("external_context") or {}).get("thestatsapi")
                 or extra_sources.get("external_context_thestatsapi"))
    apisport = (match_doc.get("api_sports")
                 or match_doc.get("apisports")
                 or extra_sources.get("apisports"))

    # ── Extraer xG desde TheStatsAPI (preferimos pre-match, luego snapshot,
    #    luego external_context). API-Sports como ultimo fallback.
    xg_pre   = _extract_xg_from_thestatsapi(ts_pre   if isinstance(ts_pre, dict)   else {})
    xg_snap  = _extract_xg_from_thestatsapi(ts_snap  if isinstance(ts_snap, dict)  else {})
    xg_ctx   = _extract_xg_from_thestatsapi(ext_ctx  if isinstance(ext_ctx, dict)  else {})
    xg_apis  = _extract_xg_from_apisports(apisport)

    xg_home = next((x for x in (xg_pre["home"], xg_snap["home"], xg_ctx["home"], xg_apis["home"]) if x is not None), None)
    xg_away = next((x for x in (xg_pre["away"], xg_snap["away"], xg_ctx["away"], xg_apis["away"]) if x is not None), None)

    forebet_ctx = _extract_forebet_context(match_doc)

    providers_used: list[str] = []
    if isinstance(ts_pre, dict) and ts_pre:
        providers_used.append("thestatsapi_enrichment")
    if isinstance(ts_snap, dict) and ts_snap:
        providers_used.append("thestatsapi_snapshot")
    if isinstance(ext_ctx, dict) and ext_ctx:
        providers_used.append("external_context_thestatsapi")
    if isinstance(apisport, dict) and apisport:
        providers_used.append("apisports")
    if forebet_ctx:
        providers_used.append("forebet")

    payload_summary = {
        "providers_used": providers_used,
        "xg": {"home": xg_home, "away": xg_away},
    }
    data_quality, dq_codes = _compute_data_quality(payload_summary)

    reason_codes: list[str] = list(dq_codes)
    if "thestatsapi_enrichment"      in providers_used:
        reason_codes.append(RC_THESTATSAPI_USED)
    if "thestatsapi_snapshot"        in providers_used:
        reason_codes.append(RC_THESTATSAPI_SNAPSHOT_USED)
    if "external_context_thestatsapi" in providers_used:
        reason_codes.append(RC_EXTERNAL_CONTEXT_USED)
    if "apisports"                   in providers_used:
        reason_codes.append(RC_APISPORTS_USED)
    if "forebet"                     in providers_used:
        reason_codes.append(RC_FOREBET_USED)

    teams = _extract_team_names(match_doc)

    # ── Market identity guard (Phase F73/F74) ──────────────────────────
    requires_mi = False
    if market_identity is not None:
        try:
            from . import market_identity_guards as _mig
            requires_mi = not _mig.has_valid_market_identity(market_identity)
        except Exception:  # noqa: BLE001
            requires_mi = False
    if requires_mi:
        reason_codes.append(RC_REQUIRES_MARKET_IDENTITY)

    canonical: dict[str, Any] = {
        "schema_version":         SCHEMA_VERSION,
        "fetched_at":             datetime.now(timezone.utc).isoformat(),
        "data_quality":           data_quality,
        "reason_codes":           reason_codes,
        "teams":                  teams,
        "xg":                     {"home": xg_home, "away": xg_away},
        "external_context":       {"forebet": forebet_ctx},
        "providers_used":         providers_used,
        "estimated_probabilities": {},
        "requires_market_identity": requires_mi,
        "market_identity":         market_identity if isinstance(market_identity, dict) else None,
    }
    return canonical


# ─────────────────────────────────────────────────────────────────────
# Mutadores seguros
# ─────────────────────────────────────────────────────────────────────
def _is_data_quality_sufficient(canonical: dict) -> bool:
    """True cuando el data_quality NO es THIN.

    Es el gate definido por producto F74 para permitir/llenar
    ``estimated_probabilities``.
    """
    dq = canonical.get("data_quality") if isinstance(canonical, dict) else None
    return _DQ_ORDER.get(dq, 0) > _DQ_ORDER[DQ_THIN]


def attach_estimated_probability(
    canonical: dict,
    identity_key: str,
    *,
    probability: float,
    method: str,
    quality: str = "USABLE",
    inputs: Optional[dict] = None,
) -> bool:
    """Adjunta una probabilidad estimada por ``market_identity_key``.

    Reglas:
      * Si ``data_quality`` es THIN → **bloquea** (devuelve False).
      * Si ``requires_market_identity`` es True → **bloquea**.
      * Si la identity_key parece UNKNOWN (``UNKNOWN:`` prefix) → bloquea.
      * Si ``quality`` es ``OBSERVE_ONLY``, se adjunta pero marcado para
        downstream (no debe alimentar edge real).

    Returns
    -------
    bool
        True si se adjuntó, False si fue bloqueado.
    """
    if not isinstance(canonical, dict):
        return False
    if canonical.get("requires_market_identity"):
        _append_code(canonical, RC_PROBABILITIES_BLOCKED_THIN
                     if not _is_data_quality_sufficient(canonical)
                     else RC_REQUIRES_MARKET_IDENTITY)
        return False
    if not _is_data_quality_sufficient(canonical):
        _append_code(canonical, RC_PROBABILITIES_BLOCKED_THIN)
        return False
    if not isinstance(identity_key, str) or not identity_key:
        return False
    if identity_key.startswith("UNKNOWN:"):
        canonical["requires_market_identity"] = True
        _append_code(canonical, RC_REQUIRES_MARKET_IDENTITY)
        return False
    p = _safe_float(probability)
    if p is None or p < 0.0 or p > 1.0:
        return False
    canonical.setdefault("estimated_probabilities", {})[identity_key] = {
        "p":       round(p, 4),
        "method":  method,
        "quality": quality,
        "inputs":  inputs or {},
    }
    return True


def _append_code(canonical: dict, code: str) -> None:
    codes = canonical.setdefault("reason_codes", [])
    if code and code not in codes:
        codes.append(code)


# ─────────────────────────────────────────────────────────────────────
# Vista pública
# ─────────────────────────────────────────────────────────────────────
__all__ = [
    "SCHEMA_VERSION",
    "DQ_THIN", "DQ_LIMITED", "DQ_USABLE", "DQ_STRONG",
    "RC_NO_PROVIDERS", "RC_THIN_DATA", "RC_PARTIAL_DATA",
    "RC_THESTATSAPI_USED", "RC_THESTATSAPI_SNAPSHOT_USED",
    "RC_EXTERNAL_CONTEXT_USED", "RC_APISPORTS_USED", "RC_FOREBET_USED",
    "RC_XG_AVAILABLE", "RC_XG_MISSING",
    "RC_REQUIRES_MARKET_IDENTITY", "RC_PROBABILITIES_BLOCKED_THIN",
    "normalize_football_enrichment",
    "attach_estimated_probability",
]
