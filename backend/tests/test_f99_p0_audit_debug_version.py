"""
test_f99_p0_audit_debug_version
================================

Fase 1 de la **Auditoría de Drift de Producción (P0)**.

Cubre el contrato del endpoint ``GET /api/debug/version`` y de los helpers
puros en ``services.debug_metadata``.

Reglas validadas:

- El endpoint **nunca** devuelve HTTP 500 (fail-soft).
- Todos los campos contractuales están presentes.
- ``metadata_source`` es coherente con la cascada acordada
  (``environment`` → ``alternative_env`` → ``git`` → ``unknown``).
- ``module_hashes`` produce SHA-256 deterministas.
- ``build_timestamp`` **NUNCA** equivale a la hora de arranque del proceso
  (no se debe confundir "reinicio" con "deploy").
- No se filtran variables sensibles (tokens, MONGO_URL, claves) en la
  respuesta.
- Header ``X-Backend-Version`` presente y coherente.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from services import debug_metadata


REPO_ROOT = Path(__file__).resolve().parents[2]  # /app


# ── Helpers puros ────────────────────────────────────────────────────────────


def test_resolve_git_sha_prefers_canonical_env(monkeypatch):
    """Variable canónica GIT_SHA tiene máxima prioridad."""
    monkeypatch.setenv("GIT_SHA", "abc123canonical")
    monkeypatch.setenv("COMMIT_SHA", "should_be_ignored")

    sha, source = debug_metadata.resolve_git_sha(repo_root=REPO_ROOT)

    assert sha == "abc123canonical"
    assert source == debug_metadata.SOURCE_ENVIRONMENT


def test_resolve_git_sha_falls_back_to_alternative_env(monkeypatch):
    """Sin GIT_SHA, usa la primera alternativa conocida."""
    monkeypatch.delenv("GIT_SHA", raising=False)
    monkeypatch.setenv("COMMIT_SHA", "alt_commit_value")

    sha, source = debug_metadata.resolve_git_sha(repo_root=REPO_ROOT)

    assert sha == "alt_commit_value"
    assert source == debug_metadata.SOURCE_ALTERNATIVE_ENV


def test_resolve_git_sha_falls_back_to_git_cli(monkeypatch):
    """Sin env vars, usa git rev-parse si .git existe."""
    monkeypatch.delenv("GIT_SHA", raising=False)
    for var in debug_metadata._ALTERNATIVE_SHA_VARS:
        monkeypatch.delenv(var, raising=False)

    sha, source = debug_metadata.resolve_git_sha(repo_root=REPO_ROOT)

    # En el entorno Preview .git existe → debe resolver vía git
    if (REPO_ROOT / ".git").exists():
        assert source == debug_metadata.SOURCE_GIT
        assert re.fullmatch(r"[0-9a-f]{40}", sha), f"expected full sha, got {sha!r}"
    else:
        assert sha == debug_metadata.UNKNOWN
        assert source == debug_metadata.SOURCE_UNKNOWN


def test_resolve_git_sha_unknown_when_no_sources(monkeypatch, tmp_path):
    """Sin env y sin .git, devuelve 'unknown' (sin lanzar excepción)."""
    monkeypatch.delenv("GIT_SHA", raising=False)
    for var in debug_metadata._ALTERNATIVE_SHA_VARS:
        monkeypatch.delenv(var, raising=False)

    # tmp_path no contiene .git
    sha, source = debug_metadata.resolve_git_sha(repo_root=tmp_path)

    assert sha == debug_metadata.UNKNOWN
    assert source == debug_metadata.SOURCE_UNKNOWN


def test_resolve_build_timestamp_prefers_canonical(monkeypatch):
    monkeypatch.setenv("BUILD_TIMESTAMP", "2026-01-01T00:00:00Z")

    ts, source = debug_metadata.resolve_build_timestamp(repo_root=REPO_ROOT)

    assert ts == "2026-01-01T00:00:00Z"
    assert source == debug_metadata.SOURCE_ENVIRONMENT


def test_resolve_build_timestamp_alternative(monkeypatch):
    monkeypatch.delenv("BUILD_TIMESTAMP", raising=False)
    monkeypatch.setenv("BUILD_TIME", "2025-12-31T23:59:59Z")

    ts, source = debug_metadata.resolve_build_timestamp(repo_root=REPO_ROOT)

    assert ts == "2025-12-31T23:59:59Z"
    assert source == debug_metadata.SOURCE_ALTERNATIVE_ENV


def test_compute_module_hashes_is_deterministic(tmp_path):
    """El mismo contenido debe producir exactamente el mismo hash."""
    f = tmp_path / "fixture.py"
    f.write_text("print('hello')\n", encoding="utf-8")

    h1 = debug_metadata.compute_module_hashes(tmp_path, modules=["fixture.py"])
    h2 = debug_metadata.compute_module_hashes(tmp_path, modules=["fixture.py"])

    assert h1 == h2
    expected = hashlib.sha256(b"print('hello')\n").hexdigest()
    assert h1["fixture.py"] == expected


def test_compute_module_hashes_marks_missing_as_missing(tmp_path):
    """Si el archivo no existe, el valor debe ser 'missing' (no None)."""
    out = debug_metadata.compute_module_hashes(tmp_path, modules=["does_not_exist.py"])

    assert out == {"does_not_exist.py": "missing"}


def test_short_sha_truncates_safely():
    full = "abc1234567890abcdef0123456789abcdef012345"
    assert debug_metadata.short_sha(full) == "abc1234"
    assert debug_metadata.short_sha(full, length=10) == "abc1234567"
    assert debug_metadata.short_sha("unknown") == "unknown"
    assert debug_metadata.short_sha("") == "unknown"


def test_build_version_payload_contract_keys():
    payload = debug_metadata.build_version_payload(repo_root=REPO_ROOT)

    required = {
        "service",
        "environment",
        "git_sha",
        "git_sha_short",
        "build_timestamp",
        "metadata_source",
        "metadata_source_detail",
        "python_version",
        "module_hashes",
        "audit_phase",
        "fetched_at",
    }
    assert required.issubset(set(payload.keys())), payload.keys()


def test_build_version_payload_metadata_source_consistency():
    """metadata_source debe ser uno de los valores válidos del contrato."""
    payload = debug_metadata.build_version_payload(repo_root=REPO_ROOT)

    valid = {
        debug_metadata.SOURCE_ENVIRONMENT,
        debug_metadata.SOURCE_ALTERNATIVE_ENV,
        debug_metadata.SOURCE_GIT,
        debug_metadata.SOURCE_UNKNOWN,
    }
    assert payload["metadata_source"] in valid
    detail = payload["metadata_source_detail"]
    assert detail["git_sha"] in valid
    assert detail["build_timestamp"] in valid


def test_build_version_payload_never_raises(monkeypatch, tmp_path):
    """Aun con entorno hostil debe devolver un dict válido."""
    # Quitar todas las env vars y usar un tmp_path sin .git
    for var in (
        "GIT_SHA",
        "BUILD_TIMESTAMP",
        *debug_metadata._ALTERNATIVE_SHA_VARS,
        *debug_metadata._ALTERNATIVE_BUILD_VARS,
        "ENVIRONMENT",
        "APP_ENV",
    ):
        monkeypatch.delenv(var, raising=False)

    payload = debug_metadata.build_version_payload(repo_root=tmp_path)

    assert payload["git_sha"] == debug_metadata.UNKNOWN
    assert payload["build_timestamp"] == debug_metadata.UNKNOWN
    assert payload["metadata_source"] == debug_metadata.SOURCE_UNKNOWN
    assert payload["module_hashes"]  # contiene rutas con valor "missing"
    assert all(v == "missing" for v in payload["module_hashes"].values())


# ── Endpoint HTTP ────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def client():
    # Import diferido para evitar coste de boot si solo se ejecutan tests puros.
    from server import app

    return TestClient(app)


def test_debug_version_endpoint_returns_200(client):
    resp = client.get("/api/debug/version")
    assert resp.status_code == 200, resp.text


def test_debug_version_endpoint_contract(client):
    resp = client.get("/api/debug/version")
    body = resp.json()

    required = {
        "service",
        "environment",
        "git_sha",
        "git_sha_short",
        "build_timestamp",
        "metadata_source",
        "metadata_source_detail",
        "python_version",
        "module_hashes",
        "audit_phase",
    }
    assert required.issubset(set(body.keys())), body


def test_debug_version_endpoint_emits_header(client):
    resp = client.get("/api/debug/version")
    sha_short = resp.json()["git_sha_short"]

    assert resp.headers.get("X-Backend-Version") == sha_short
    assert "no-store" in resp.headers.get("Cache-Control", "")


def test_debug_version_endpoint_does_not_leak_secrets(client):
    """La respuesta no debe contener tokens, MONGO_URL ni patrones de keys."""
    resp = client.get("/api/debug/version")
    raw = json.dumps(resp.json()).lower()

    forbidden_substrings = [
        "mongo_url",
        "mongodb://",
        "mongodb+srv://",
        "secret",
        "password",
        "api_key",
        "bearer ",
        "authorization",
    ]
    for needle in forbidden_substrings:
        assert needle not in raw, (
            f"Posible filtración de secreto en /api/debug/version: '{needle}'"
        )


def test_debug_version_module_hashes_are_sha256(client):
    """Todos los valores deben ser SHA-256 hex (64 hex chars) o 'missing'."""
    resp = client.get("/api/debug/version")
    hashes = resp.json()["module_hashes"]

    assert hashes, "module_hashes vacío"
    for rel_path, digest in hashes.items():
        if digest == "missing":
            continue
        assert re.fullmatch(r"[0-9a-f]{64}", digest), (
            f"Hash inválido para {rel_path}: {digest!r}"
        )


def test_debug_version_build_timestamp_is_not_process_start(client):
    """
    Regla crítica: build_timestamp jamás debe coincidir con la hora de
    arranque del proceso. En Preview con .git debe devolver el timestamp
    del commit HEAD, no `now()`.
    """
    from datetime import datetime, timezone, timedelta

    resp = client.get("/api/debug/version")
    ts = resp.json()["build_timestamp"]

    if ts == debug_metadata.UNKNOWN:
        # Sin entorno ni .git no podemos validar; pero el test sigue verde.
        return

    # Si el timestamp viene de git, lo más probable es que NO esté dentro
    # de los últimos 10 segundos (el commit fue hecho antes del boot).
    try:
        parsed = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        pytest.fail(f"build_timestamp no es ISO-8601 válido: {ts!r}")

    now = datetime.now(timezone.utc)
    delta = abs((now - parsed).total_seconds())
    # Ventana de seguridad: si está a menos de 5s del now() probablemente
    # alguien lo está poniendo en runtime, lo cual viola el contrato.
    assert delta > 5, (
        f"build_timestamp sospechosamente cercano a now() (Δ={delta:.2f}s). "
        "No debe derivarse de la hora de arranque del servidor."
    )
