"""
test_f99_p0_audit_debug_sources
================================

Fase 4 de la **Auditoría de Drift de Producción (P0)**.

Reglas validadas:

- ``GET /api/debug/sources`` jamás devuelve HTTP 500.
- Contrato de respuesta estable (``summary``, ``sources``, ``valid_statuses``).
- **Invariante crítica**: ``api_sports`` **nunca** aparece como ``ENABLED``
  en contexto fútbol (post-F99.2).
- Cada entry usa un ``status`` perteneciente al conjunto canónico
  ``{REGISTERED, ENABLED, DISABLED, UNAVAILABLE}``.
- No se filtran secretos.
- El header ``Cache-Control: no-store`` está presente.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from services import debug_sources


@pytest.fixture(scope="module")
def client():
    from server import app

    return TestClient(app)


# ── Helpers puros ────────────────────────────────────────────────────────────


def test_valid_statuses_are_canonical():
    """El conjunto de statuses válidos coincide con el contrato acordado."""
    assert debug_sources.VALID_STATUSES == {
        "REGISTERED",
        "ENABLED",
        "DISABLED",
        "UNAVAILABLE",
    }


def test_build_sources_payload_shape():
    payload = debug_sources.build_sources_payload()

    assert "audit_phase" in payload
    assert payload["audit_phase"] == "F99-P0-PRODUCTION-DRIFT-AUDIT"
    assert "summary" in payload
    assert "sources" in payload
    assert isinstance(payload["sources"], list)
    assert payload["sources"], "lista de fuentes vacía"


def test_build_sources_payload_status_is_canonical():
    payload = debug_sources.build_sources_payload()
    for entry in payload["sources"]:
        assert entry["status"] in debug_sources.VALID_STATUSES, (
            f"status no canónico para {entry.get('name')!r}: {entry.get('status')!r}"
        )


def test_api_sports_is_disabled_in_football():
    """
    Invariante crítica: api_sports NUNCA puede aparecer como ENABLED en
    el scope fútbol post-F99.2.
    """
    payload = debug_sources.build_sources_payload()
    api_sports = next(
        (s for s in payload["sources"] if s.get("name") == "api_sports"),
        None,
    )
    assert api_sports is not None, "api_sports no fue registrado en el inventario"
    assert "football" in (api_sports.get("sport_scope") or [])
    assert api_sports["status"] == debug_sources.STATUS_DISABLED, (
        f"api_sports debe ser DISABLED en fútbol post-F99.2, "
        f"got {api_sports['status']!r}"
    )


def test_build_sources_payload_does_not_leak_secrets():
    payload = debug_sources.build_sources_payload()
    raw = json.dumps(payload).lower()
    for needle in (
        "mongo_url",
        "mongodb://",
        "mongodb+srv://",
        "password",
        "api_key",
        "bearer ",
        "secret",
        "authorization",
    ):
        assert needle not in raw, (
            f"Posible filtración de secreto en /api/debug/sources: '{needle}'"
        )


def test_build_sources_payload_summary_counts_match():
    """summary.by_status debe sumar igual a summary.total."""
    payload = debug_sources.build_sources_payload()
    by_status = payload["summary"]["by_status"]
    total = payload["summary"]["total"]
    assert sum(by_status.values()) == total == len(payload["sources"])


def test_invariant_enforcement_when_inspector_lies(monkeypatch):
    """
    Si por error futuro un inspector devolviera api_sports=ENABLED en
    fútbol, la capa de invariantes debe forzar DISABLED y registrar el
    flag ``enforced_invariant``.
    """

    def fake_inspector():
        return {
            "name": "api_sports",
            "sport_scope": ["football"],
            "status": debug_sources.STATUS_ENABLED,  # ❌ violación
            "notes": "[test] forzar violación",
            "diagnostics": {},
        }

    monkeypatch.setattr(
        debug_sources,
        "_INSPECTORS",
        (fake_inspector,) + tuple(
            i for i in debug_sources._INSPECTORS if i.__name__ != "_inspect_api_sports"
        ),
    )

    payload = debug_sources.build_sources_payload()
    api_sports = next(s for s in payload["sources"] if s.get("name") == "api_sports")
    assert api_sports["status"] == debug_sources.STATUS_DISABLED
    assert api_sports.get("enforced_invariant") == (
        "api_sports_must_not_be_enabled_in_football_post_F99.2"
    )


# ── Endpoint HTTP ────────────────────────────────────────────────────────────


def test_debug_sources_endpoint_returns_200(client):
    resp = client.get("/api/debug/sources")
    assert resp.status_code == 200, resp.text


def test_debug_sources_endpoint_has_no_store_cache(client):
    resp = client.get("/api/debug/sources")
    assert "no-store" in resp.headers.get("Cache-Control", "")


def test_debug_sources_endpoint_contract(client):
    resp = client.get("/api/debug/sources")
    body = resp.json()

    assert {"audit_phase", "summary", "sources", "valid_statuses"}.issubset(body.keys())
    assert isinstance(body["sources"], list)
    assert body["sources"]


def test_debug_sources_endpoint_api_sports_is_disabled(client):
    """Smoke test end-to-end de la invariante crítica."""
    resp = client.get("/api/debug/sources")
    body = resp.json()

    api_sports = next((s for s in body["sources"] if s.get("name") == "api_sports"), None)
    assert api_sports is not None
    assert api_sports["status"] == "DISABLED"


def test_debug_sources_endpoint_no_secret_keys(client):
    """End-to-end: la respuesta JSON no contiene tokens ni urls privadas."""
    resp = client.get("/api/debug/sources")
    raw = resp.text.lower()
    for needle in (
        "mongo_url",
        "mongodb://",
        "mongodb+srv://",
        "password",
        "bearer ",
        "authorization",
    ):
        assert needle not in raw
