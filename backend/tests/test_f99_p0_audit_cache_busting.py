"""
test_f99_p0_audit_cache_busting
===============================

Fase 7 de la **Auditoría de Drift de Producción (P0)**.

Reglas validadas:

- El middleware aplica ``Cache-Control: no-store`` a endpoints dinámicos
  (``/api/analysis/run``, ``/api/analysis/jobs/*``, ``/api/debug/*``).
- El middleware aplica ``Cache-Control: no-store`` cuando la URL incluye
  ``refresh=true`` (o variantes equivalentes).
- El middleware NO altera ``Cache-Control`` en endpoints estáticos
  (regresión: no rompemos caches útiles).
- El endpoint ``/api/debug/version`` y ``/api/debug/sources`` ya emiten
  su propio ``no-store`` (no debe ser sobreescrito por uno menos
  restrictivo).
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(scope="module")
def client():
    from server import app

    return TestClient(app)


def test_debug_version_emits_no_store(client):
    resp = client.get("/api/debug/version")
    assert "no-store" in resp.headers.get("Cache-Control", "")


def test_debug_sources_emits_no_store(client):
    resp = client.get("/api/debug/sources")
    assert "no-store" in resp.headers.get("Cache-Control", "")


def test_refresh_true_triggers_no_store_on_arbitrary_endpoint(client):
    """
    Si la query string incluye `refresh=true`, el middleware fuerza no-store
    incluso si el endpoint subyacente no lo configura.
    """
    # Endpoint público que existe; aunque devuelva 401/404, el header igual aplica.
    resp = client.get("/api/odds/snapshots/some_id?refresh=true")
    assert "no-store" in resp.headers.get("Cache-Control", "").lower()


def test_no_refresh_does_not_force_no_store_on_arbitrary_endpoint(client):
    """
    Sin refresh=true y sin ser un path dinámico conocido, el middleware
    NO debe forzar no-store (no rompemos caches útiles).
    """
    resp = client.get("/api/odds/snapshots/some_id")
    cc = resp.headers.get("Cache-Control", "")
    # El endpoint puede o no haber puesto algo, pero el middleware no
    # debe haberlo *forzado*.  Si lo emitió, debe venir del endpoint.
    # Regla: NO esperamos no-store automáticamente aquí.
    assert "no-store" not in cc.lower(), (
        f"middleware emitió no-store en endpoint estático sin refresh=true: {cc!r}"
    )


def test_x_backend_version_header_consistent_on_dynamic_endpoint(client):
    resp = client.get("/api/debug/version?refresh=true")
    assert resp.headers.get("X-Backend-Version")
    assert "no-store" in resp.headers.get("Cache-Control", "")
