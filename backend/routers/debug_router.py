"""
debug_router
============

Router HTTP de **diagnóstico de build** para la Auditoría de Drift Producción.

Endpoints expuestos (todos bajo el prefijo ``/api/debug``):

- ``GET /api/debug/version``
  Devuelve la identidad del backend en runtime (sha, build_timestamp,
  metadata_source, module_hashes, python_version, environment).
  Nunca devuelve 500: en peor caso emite valores ``"unknown"``.

Reglas obligatorias (acordadas con el usuario):

- **Sin secretos**: no se devuelven tokens, URLs privadas ni configuración
  completa.  Solo metadatos opacos.
- Hashes deterministas SHA-256 de módulos críticos.
- ``metadata_source`` indica si los datos vienen de variables canónicas,
  alternativas conocidas, ``git`` CLI o ``"unknown"``.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from services import debug_metadata, debug_sources

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/debug", tags=["debug"])


@router.get("/version")
async def get_debug_version() -> JSONResponse:
    """
    Identidad de build del backend.

    Fail-soft: cualquier excepción interna es capturada y registrada; la
    respuesta siempre es HTTP 200 con un payload válido (posiblemente con
    valores ``"unknown"``).  Esto es intencional para que el endpoint sea
    una **fuente de verdad confiable** durante incidentes de drift.
    """
    try:
        payload = debug_metadata.build_version_payload()
    except Exception as exc:  # noqa: BLE001 — el endpoint debe ser fail-soft
        log.exception("[debug/version] payload construction failed: %s", exc)
        payload = {
            "service": debug_metadata.SERVICE_NAME,
            "environment": debug_metadata.UNKNOWN,
            "git_sha": debug_metadata.UNKNOWN,
            "git_sha_short": debug_metadata.UNKNOWN,
            "build_timestamp": debug_metadata.UNKNOWN,
            "metadata_source": debug_metadata.SOURCE_UNKNOWN,
            "metadata_source_detail": {
                "git_sha": debug_metadata.SOURCE_UNKNOWN,
                "build_timestamp": debug_metadata.SOURCE_UNKNOWN,
            },
            "module_hashes": {},
            "audit_phase": "F99-P0-PRODUCTION-DRIFT-AUDIT",
            "error": "PAYLOAD_BUILD_FAILED",
        }

    headers = {
        "X-Backend-Version": str(payload.get("git_sha_short") or debug_metadata.UNKNOWN),
        "Cache-Control": "no-store, max-age=0",
    }
    return JSONResponse(content=payload, headers=headers)


@router.get("/sources")
async def get_debug_sources() -> JSONResponse:
    """
    Inventario runtime de proveedores de datos.

    Fail-soft: cualquier excepción interna se convierte en respuesta 200
    con payload mínimo y campo ``error``.  El header
    ``Cache-Control: no-store`` previene que un proxy o CDN sirva un
    snapshot obsoleto del registro de fuentes.
    """
    try:
        payload = debug_sources.build_sources_payload()
    except Exception as exc:  # noqa: BLE001
        log.exception("[debug/sources] payload construction failed: %s", exc)
        payload = {
            "audit_phase": "F99-P0-PRODUCTION-DRIFT-AUDIT",
            "summary": {"total": 0, "by_status": {}},
            "sources": [],
            "valid_statuses": sorted(debug_sources.VALID_STATUSES),
            "error": "PAYLOAD_BUILD_FAILED",
        }

    headers = {"Cache-Control": "no-store, max-age=0"}
    return JSONResponse(content=payload, headers=headers)


__all__ = ["router"]
