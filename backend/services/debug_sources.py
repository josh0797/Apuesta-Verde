"""
debug_sources
=============

Construye el inventario runtime de proveedores de datos del backend para
el endpoint ``/api/debug/sources`` (Fase 4 — Auditoría Drift Producción).

Estados posibles (acordados con el usuario):

- ``REGISTERED``: la fuente está declarada en el código pero no se ha
  determinado si actualmente está habilitada.
- ``ENABLED``: la fuente está habilitada y participa en al menos un path
  activo de ingestión/enriquecimiento.
- ``DISABLED``: la fuente está declarada pero **intencionalmente apagada**
  (p. ej. ``api_sports`` post-F99.2 en el contexto fútbol).  No participa
  en runtime, no llena traces ni provenance.
- ``UNAVAILABLE``: la fuente está habilitada por configuración pero el
  cliente reporta inaccesibilidad (sin credenciales, sin red, etc.).

Reglas:

- **No exponer secretos**: nunca incluir tokens, URLs privadas, claves o
  configuración completa.  Solo metadatos opacos: nombre, estado, sport
  y notas en lenguaje natural.

- **Determinista**: el output debe ser estable entre llamadas siempre que
  el código y la configuración no cambien.

- **Fail-soft**: si un check falla, la fuente se reporta como
  ``UNAVAILABLE`` con ``error`` opaco.
"""

from __future__ import annotations

import importlib
import logging
import os
from typing import Dict, List, Optional

log = logging.getLogger(__name__)


# Estados canónicos
STATUS_REGISTERED: str = "REGISTERED"
STATUS_ENABLED: str = "ENABLED"
STATUS_DISABLED: str = "DISABLED"
STATUS_UNAVAILABLE: str = "UNAVAILABLE"

VALID_STATUSES = {STATUS_REGISTERED, STATUS_ENABLED, STATUS_DISABLED, STATUS_UNAVAILABLE}


def _has_module(dotted: str) -> bool:
    """True si el módulo es importable (sin ejecutar side effects relevantes)."""
    try:
        importlib.import_module(dotted)
        return True
    except Exception:
        return False


def _has_env(name: str) -> bool:
    raw = os.environ.get(name, "")
    return bool(raw and raw.strip())


def _read_flag(name: str, default: str = "true") -> bool:
    """Lee una flag booleana de entorno (true/1/yes)."""
    raw = (os.environ.get(name, default) or "").strip().lower()
    return raw in ("1", "true", "yes", "on")


# ── Inspección por proveedor ────────────────────────────────────────────────


def _inspect_api_sports() -> Dict[str, object]:
    """
    `api_sports` (API-Football) — declarada DISABLED en fútbol tras F99.2.

    La declaración explícita de DISABLED es clave para que la auditoría
    diferencie "no existe" vs "existe pero apagada intencionalmente".
    """
    # F99.2: api_football.py es un stub fail-closed.  Validamos que el módulo
    # se importa pero también que la flag (si existe) está apagada.
    module_present = _has_module("services.api_football")
    kill_switch = _read_flag("DISABLE_API_FOOTBALL", default="true")
    return {
        "name": "api_sports",
        "aliases": ["api-football", "api-sports", "API-Football"],
        "sport_scope": ["football"],
        "status": STATUS_DISABLED,
        "notes": (
            "Apagada intencionalmente en fútbol (F99.2). El módulo "
            "`services.api_football` es un stub fail-closed (no IO). "
            "Conservado para compatibilidad de imports; sin participación "
            "en ningún path de enriquecimiento activo."
        ),
        "diagnostics": {
            "module_importable": module_present,
            "kill_switch_active": kill_switch,
            "stub_fail_closed": True,
        },
    }


def _inspect_sofascore() -> Dict[str, object]:
    """SofaScore — primario en fútbol post-F99."""
    module_present = (
        _has_module("services.external_sources.sofascore")
        or _has_module("services.adapters.sofascore_adapter")
        or _has_module("services.football_sofascore_hydrator")
    )
    return {
        "name": "sofascore",
        "aliases": ["SofaScore"],
        "sport_scope": ["football"],
        "status": STATUS_ENABLED if module_present else STATUS_UNAVAILABLE,
        "notes": (
            "Proveedor primario fútbol post-F99: fixtures, lineups, "
            "team stats, h2h, odds (vía aggregator)."
        ),
        "diagnostics": {
            "module_importable": module_present,
        },
    }


def _inspect_thestatsapi() -> Dict[str, object]:
    module_present = (
        _has_module("services.external_sources.thestatsapi_football_enrichment")
        or _has_module("services.external_sources.thestatsapi_odds_adapter")
    )
    return {
        "name": "thestatsapi",
        "aliases": ["TheStatsAPI"],
        "sport_scope": ["football"],
        "status": STATUS_ENABLED if module_present else STATUS_UNAVAILABLE,
        "notes": "Fallback estructural fútbol (F84): team_stats, h2h, odds.",
        "diagnostics": {"module_importable": module_present},
    }


def _inspect_theoddsapi() -> Dict[str, object]:
    module_present = (
        _has_module("services.football_odds_aggregator")
        or _has_module("services.external_sources.theoddsapi_client")
    )
    return {
        "name": "theoddsapi",
        "aliases": ["TheOddsAPI"],
        "sport_scope": ["football", "baseball", "basketball"],
        "status": STATUS_ENABLED if module_present else STATUS_UNAVAILABLE,
        "notes": "Aggregator de odds (F99.5). Participa en cascada con SofaScore + TheStatsAPI.",
        "diagnostics": {"module_importable": module_present},
    }


def _inspect_sportytrader() -> Dict[str, object]:
    module_present = _has_module("services.sportytrader_scraper")
    flag_on = _read_flag("ENABLE_SPORTYTRADER", default="true")
    return {
        "name": "sportytrader",
        "aliases": ["SportyTrader"],
        "sport_scope": ["football"],
        "status": STATUS_ENABLED if (module_present and flag_on) else (
            STATUS_DISABLED if module_present else STATUS_UNAVAILABLE
        ),
        "notes": "Editorial externo + odds fallback vía scrape.do (fail-soft).",
        "diagnostics": {
            "module_importable": module_present,
            "flag_enabled": flag_on,
        },
    }


def _inspect_thesportsdb() -> Dict[str, object]:
    module_present = (
        _has_module("services.external_sources.thesportsdb_client")
        or _has_module("services.thesportsdb_client")
    )
    return {
        "name": "thesportsdb",
        "aliases": ["TheSportsDB"],
        "sport_scope": ["football"],
        "status": STATUS_ENABLED if module_present else STATUS_REGISTERED,
        "notes": "Catálogo de equipos/ligas y branding fútbol.",
        "diagnostics": {"module_importable": module_present},
    }


def _inspect_forebet() -> Dict[str, object]:
    module_present = (
        _has_module("services.forebet_scraper")
        or _has_module("services.external_sources.forebet_client")
    )
    return {
        "name": "forebet",
        "aliases": ["Forebet"],
        "sport_scope": ["football"],
        "status": STATUS_ENABLED if module_present else STATUS_REGISTERED,
        "notes": "Predicciones externas (F70/F72) — auditadas, no autoritativas.",
        "diagnostics": {"module_importable": module_present},
    }


def _inspect_mlb_stats_api() -> Dict[str, object]:
    module_present = _has_module("services.mlb_stats_api")
    return {
        "name": "mlb_stats_api",
        "aliases": ["MLB Stats API"],
        "sport_scope": ["baseball"],
        "status": STATUS_ENABLED if module_present else STATUS_REGISTERED,
        "notes": "Fuente primaria oficial MLB.",
        "diagnostics": {"module_importable": module_present},
    }


def _inspect_espn() -> Dict[str, object]:
    module_present = (
        _has_module("services.external_sources.espn_mlb")
        or _has_module("services.external_sources.espn_client")
    )
    return {
        "name": "espn",
        "aliases": ["ESPN"],
        "sport_scope": ["basketball", "baseball"],
        "status": STATUS_ENABLED if module_present else STATUS_REGISTERED,
        "notes": "Fallback NBA/MLB (scoreboard, box scores).",
        "diagnostics": {"module_importable": module_present},
    }


def _inspect_statsbomb() -> Dict[str, object]:
    module_present = (
        _has_module("services.adapters.statsbomb_adapter")
        or _has_module("services.external_sources.statsbomb_client")
    )
    return {
        "name": "statsbomb",
        "aliases": ["StatsBomb"],
        "sport_scope": ["football"],
        "status": STATUS_REGISTERED if module_present else STATUS_UNAVAILABLE,
        "notes": "xG público (F99.8 pendiente). Background-first.",
        "diagnostics": {"module_importable": module_present},
    }


def _inspect_fbref() -> Dict[str, object]:
    module_present = (
        _has_module("services.adapters.fbref_adapter")
        or _has_module("services.external_sources.fbref_client")
        or _has_module("services.external_sources.fbref")
    )
    return {
        "name": "fbref",
        "aliases": ["FBref"],
        "sport_scope": ["football"],
        "status": STATUS_REGISTERED if module_present else STATUS_UNAVAILABLE,
        "notes": "xG público + minutos (F85). Background-first vía scrape.do.",
        "diagnostics": {"module_importable": module_present},
    }


def _inspect_score365() -> Dict[str, object]:
    module_present = _has_module("services.external_sources.score365_trends_client")
    return {
        "name": "score365",
        "aliases": ["365Scores", "Score365"],
        "sport_scope": ["football"],
        "status": STATUS_ENABLED if module_present else STATUS_REGISTERED,
        "notes": "Trends + tendencias H2H (F82).",
        "diagnostics": {"module_importable": module_present},
    }


# Tabla de inspectores
_INSPECTORS = (
    _inspect_api_sports,
    _inspect_sofascore,
    _inspect_thestatsapi,
    _inspect_theoddsapi,
    _inspect_sportytrader,
    _inspect_thesportsdb,
    _inspect_forebet,
    _inspect_mlb_stats_api,
    _inspect_espn,
    _inspect_statsbomb,
    _inspect_fbref,
    _inspect_score365,
)


def build_sources_payload() -> Dict[str, object]:
    """
    Construye el payload de ``/api/debug/sources``.

    Fail-soft global.  Cada inspector individual también es fail-soft: si
    lanza, la fuente se reporta como ``UNAVAILABLE`` con campo ``error``.
    """
    sources: List[Dict[str, object]] = []
    counts = {s: 0 for s in VALID_STATUSES}

    for inspector in _INSPECTORS:
        try:
            entry = inspector()
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "[debug_sources] inspector %s failed: %s",
                getattr(inspector, "__name__", "<anon>"),
                exc,
            )
            entry = {
                "name": getattr(inspector, "__name__", "unknown").replace("_inspect_", ""),
                "status": STATUS_UNAVAILABLE,
                "error": "INSPECTOR_FAILED",
                "notes": "Inspector lanzó excepción; reportado como UNAVAILABLE.",
            }

        # Sanitización mínima
        status = entry.get("status", STATUS_REGISTERED)
        if status not in VALID_STATUSES:
            status = STATUS_REGISTERED
            entry["status"] = status

        counts[status] += 1
        sources.append(entry)

    # Validación de invariantes críticas:
    # `api_sports` (en fútbol) jamás debe aparecer como ENABLED.
    for s in sources:
        if s.get("name") == "api_sports" and "football" in (s.get("sport_scope") or []):
            if s.get("status") == STATUS_ENABLED:
                # Forzar la corrección y registrar.
                log.error("[debug_sources] api_sports figuraba como ENABLED en fútbol — forzado a DISABLED")
                s["status"] = STATUS_DISABLED
                s["enforced_invariant"] = "api_sports_must_not_be_enabled_in_football_post_F99.2"
                counts[STATUS_ENABLED] -= 1
                counts[STATUS_DISABLED] += 1

    return {
        "audit_phase": "F99-P0-PRODUCTION-DRIFT-AUDIT",
        "summary": {
            "total": len(sources),
            "by_status": counts,
        },
        "sources": sources,
        "valid_statuses": sorted(VALID_STATUSES),
    }


__all__ = [
    "build_sources_payload",
    "STATUS_REGISTERED",
    "STATUS_ENABLED",
    "STATUS_DISABLED",
    "STATUS_UNAVAILABLE",
    "VALID_STATUSES",
]
