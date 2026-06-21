"""Pytest conftest — Sprint-D9-HOTFIX2.

Provee defaults seguros para todas las suites de la app, garantizando
que los tests preexistentes (que asumen API-Football habilitada) sigan
pasando aunque el ``.env`` de producción ahora lo desactive
permanentemente (``ENABLE_API_FOOTBALL_FALLBACK=false``).

Los tests que necesitan validar el path "API-Football off" pueden
sobrescribir el flag explícitamente con ``monkeypatch.setenv``.
"""
from __future__ import annotations

import os

import pytest


@pytest.fixture(autouse=True)
def _enable_api_football_for_tests(monkeypatch):
    """Por default, las suites operan asumiendo API-Football habilitada,
    porque la mayor parte de los tests del repo se escribieron antes
    del Sprint-D9-HOTFIX2 (cuando la API estaba activa).

    Tests específicos que quieren validar el path "API-Football OFF"
    pueden hacer ``monkeypatch.setenv("ENABLE_API_FOOTBALL_FALLBACK",
    "false")`` y este fixture no interferirá (pytest aplica los
    overrides del test después del autouse).
    """
    # Solo seteamos si el test/.env no lo ha tocado.
    if not os.environ.get("ENABLE_API_FOOTBALL_FALLBACK_TEST_OVERRIDE"):
        monkeypatch.setenv("ENABLE_API_FOOTBALL_FALLBACK", "true")
    yield
