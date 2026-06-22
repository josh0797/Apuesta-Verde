"""
test_f99_p0_audit_backend_version_meta
======================================

Fase 3 de la **Auditoría de Drift de Producción (P0)**.

Valida:

- El middleware global inyecta el header ``X-Backend-Version`` en **toda**
  respuesta saliente (incluso para endpoints no relacionados con la audit).
- El helper ``_build_response_meta()`` produce un bloque
  ``backend_version`` con el contrato acordado.
- ``_build_response_meta()`` es fail-soft: jamás lanza excepción.
"""

from __future__ import annotations

import re

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(scope="module")
def client():
    from server import app

    return TestClient(app)


def test_middleware_injects_x_backend_version_on_debug_version(client):
    resp = client.get("/api/debug/version")
    sha = resp.headers.get("X-Backend-Version", "")
    assert sha, "X-Backend-Version header is missing"
    # Debe ser SHA corto (7 hex) o "unknown"
    assert sha == "unknown" or re.fullmatch(r"[0-9a-f]{7}", sha), (
        f"X-Backend-Version no parece SHA corto válido: {sha!r}"
    )


def test_middleware_injects_x_backend_version_on_unrelated_endpoint(client):
    """Cualquier endpoint, incluso sin auth, debe traer el header."""
    # Endpoint sin auth requerido: api/odds/snapshots/<nonexistent> → probablemente 404
    # Igualmente el middleware debe inyectar header en la respuesta.
    resp = client.get("/api/odds/snapshots/__nonexistent_match_id__")
    assert "X-Backend-Version" in resp.headers, (
        "El middleware no inyectó X-Backend-Version en endpoint arbitrario"
    )


def test_middleware_x_backend_version_is_consistent(client):
    """Dos llamadas consecutivas deben devolver el mismo SHA."""
    r1 = client.get("/api/debug/version")
    r2 = client.get("/api/debug/version")
    assert r1.headers["X-Backend-Version"] == r2.headers["X-Backend-Version"]


def test_build_response_meta_contract():
    """El helper ``_build_response_meta`` debe respetar el contrato."""
    from server import _build_response_meta

    meta = _build_response_meta()

    assert "backend_version" in meta
    bv = meta["backend_version"]
    required = {
        "git_sha",
        "git_sha_short",
        "build_timestamp",
        "metadata_source",
        "audit_phase",
    }
    assert required.issubset(set(bv.keys())), bv
    assert isinstance(bv["metadata_source"], dict)
    assert {"git_sha", "build_timestamp"}.issubset(bv["metadata_source"].keys())
    assert bv["audit_phase"] == "F99-P0-PRODUCTION-DRIFT-AUDIT"


def test_build_response_meta_never_raises(monkeypatch):
    """Aun con `debug_metadata` lanzando excepción, debe devolver un dict válido."""
    import server as _srv
    from services import debug_metadata as _dm

    def _boom(*args, **kwargs):
        raise RuntimeError("simulated failure for test")

    monkeypatch.setattr(_dm, "resolve_git_sha", _boom)

    meta = _srv._build_response_meta()
    assert "backend_version" in meta
    bv = meta["backend_version"]
    assert bv["git_sha"] == "unknown"
    assert bv["build_timestamp"] == "unknown"
    assert bv["audit_phase"] == "F99-P0-PRODUCTION-DRIFT-AUDIT"


def test_build_response_meta_sha_matches_debug_version_endpoint(client):
    """El sha en `_meta.backend_version` debe coincidir con `/api/debug/version`."""
    from server import _build_response_meta

    meta = _build_response_meta()
    sha_from_helper = meta["backend_version"]["git_sha"]

    resp = client.get("/api/debug/version")
    sha_from_endpoint = resp.json()["git_sha"]

    assert sha_from_helper == sha_from_endpoint, (
        f"Drift entre helper ({sha_from_helper!r}) y endpoint ({sha_from_endpoint!r})"
    )
