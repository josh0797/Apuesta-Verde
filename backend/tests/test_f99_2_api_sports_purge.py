"""Sprint-F99.2 · Purga estructural de API-Sports en data_ingestion.

Guardas binding del usuario:

  1. ``grep`` de call-sites activos ``af.*`` en ``data_ingestion.py`` = 0.
  2. El stub ``api_football`` hace **cero IO** y queda fail-closed.
  3. Los caches ``cache_*`` legacy NO reciben nuevas escrituras.
  4. No se escriben nuevos registros con provenance ``api_football``
     (el path activo emite ``API_FOOTBALL_DEPRECATED_STUB_USED`` en su
     lugar).
  5. Fallo de TheSportsDB NO reactiva API-Sports.
"""
from __future__ import annotations

import re
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

import services.api_football as af


_DATA_INGESTION_PATH = (
    Path(__file__).resolve().parent.parent
    / "services" / "data_ingestion.py"
)
_API_FOOTBALL_PATH = (
    Path(__file__).resolve().parent.parent
    / "services" / "api_football.py"
)


# ─────────────────────────────────────────────────────────────────────
# 1. Zero active call-sites in data_ingestion.py (grep guard)
# ─────────────────────────────────────────────────────────────────────
def test_no_active_af_callsites_in_data_ingestion():
    """``await af.<fn>(...)`` and ``af.<fn>(`` must not appear as live code.

    Comments and docstrings are allowed (we strip them before grepping).
    """
    src = _DATA_INGESTION_PATH.read_text(encoding="utf-8")
    # Remove single-line comments AFTER code (e.g. ``x = 1  # uses af.foo``).
    cleaned_lines = []
    for line in src.splitlines():
        stripped = line.lstrip()
        # Full-line comment? skip.
        if stripped.startswith("#"):
            continue
        # Trailing comment? cut it off.
        if "#" in line:
            line = re.split(r"\s+#", line, maxsplit=1)[0]
        cleaned_lines.append(line)
    cleaned = "\n".join(cleaned_lines)
    # Also strip triple-quoted blocks (docstrings) to be safe.
    cleaned = re.sub(r'""".*?"""', "", cleaned, flags=re.DOTALL)
    cleaned = re.sub(r"'''.*?'''", "", cleaned, flags=re.DOTALL)

    # Patterns we want to be ABSENT from live code.
    patterns = [
        r"\baf\.fixtures_by_date\(",
        r"\baf\.fixtures_next_48h\(",
        r"\baf\.fixtures_live\(",
        r"\baf\.fixtures_by_league_window\(",
        r"\baf\.fixture_by_id\(",
        r"\baf\.odds_for_fixture\(",
        r"\baf\.team_statistics\(",
        r"\baf\.standings\(",
        r"\baf\.head_to_head\(",
        r"\baf\.injuries\(",
        r"\baf\.fixture_statistics\(",
        r"\baf\.team_corner_form\(",
        r"\baf\.fixtures_last_n\(",
    ]
    offenders = []
    for pat in patterns:
        if re.search(pat, cleaned):
            offenders.append(pat)
    assert offenders == [], (
        "F99.2 viola la regla de cero call-sites activos. Patrones encontrados: "
        + ", ".join(offenders)
    )


# ─────────────────────────────────────────────────────────────────────
# 2. Stub api_football — fail-closed sin IO
# ─────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_stub_get_does_zero_io_and_returns_empty_envelope(monkeypatch):
    """``_get`` MUST short-circuit regardless of env flag. If the client
    is touched, the test fails (exploding mock client)."""
    # Even if the kill-switch flag is OFF the stub must still fail-closed.
    monkeypatch.delenv(af.DISABLE_FLAG_ENV_VAR, raising=False)
    monkeypatch.setattr(af, "API_KEY", "fake-key-should-not-be-used")

    class _ExplodingClient:
        async def get(self, *_a, **_k):
            raise AssertionError("F99.2: stub never performs HTTP IO")

    out = await af._get(_ExplodingClient(), "/fixtures", params={"date": "2025-01-01"})
    assert out["response"]              == []
    assert out["errors"]                == {}
    assert out["_f99_disabled"]         is True
    assert out["_f99_deprecated_stub"]  is True
    assert out["_reason_code"]          == af.DEPRECATED_STUB_REASON_CODE


@pytest.mark.asyncio
async def test_stub_increments_usage_counter():
    # Reset counters for a clean run.
    for k in af.DEPRECATED_STUB_USAGE_COUNTERS:
        af.DEPRECATED_STUB_USAGE_COUNTERS[k] = 0
    out1 = await af.fixtures_by_date(MagicMock(), "2025-01-01")
    out2 = await af.team_statistics(MagicMock(), 1, 2)
    assert out1 == []
    assert out2 == {}
    assert af.DEPRECATED_STUB_USAGE_COUNTERS["fixtures_by_date"] >= 1
    assert af.DEPRECATED_STUB_USAGE_COUNTERS["team_statistics"]  >= 1
    # _get gets bumped on every call too.
    assert af.DEPRECATED_STUB_USAGE_COUNTERS["_get"] >= 2


@pytest.mark.asyncio
async def test_stub_cache_set_is_noop(monkeypatch):
    """``_cache_set`` MUST NOT write new entries into legacy caches."""
    fake_coll = MagicMock()
    fake_coll.update_one = AsyncMock(side_effect=AssertionError(
        "F99.2: cache_set must be a no-op; no new writes are allowed."
    ))
    fake_db = {"cache_team_stats": fake_coll}

    class _DB:
        def __getitem__(self, k):
            return fake_db[k]

    await af._cache_set(_DB(), "cache_team_stats", {"k": 1}, {"data": "x"})
    # If update_one was reached the assertion above would have fired.


@pytest.mark.asyncio
async def test_stub_all_public_endpoints_fail_closed():
    """Every public endpoint returns its documented empty shape."""
    client = MagicMock()
    assert await af.fixtures_by_date(client, "2025-01-01") == []
    assert await af.fixtures_live(client)                  == []
    assert await af.fixtures_by_league_window(client, 1, 2024,
                                                 from_date="2025-01-01",
                                                 to_date="2025-01-02")    == []
    assert await af.fixture_by_id(client, 999)                          is None
    assert await af.odds_for_fixture(client, 999, db=None)              == []
    assert await af.team_statistics(client, 1, 2, db=None)              == {}
    assert await af.standings(client, 1, db=None)                       == []
    assert await af.head_to_head(client, 1, 2, db=None)                 == []
    assert await af.injuries(client, 1, db=None)                        == []
    assert await af.fixture_statistics(client, 1, db=None)              == []
    assert await af.fixtures_last_n(client, 1, db=None)                 == []


# ─────────────────────────────────────────────────────────────────────
# 3. Legacy caches no participan en rankings activos del cascade
# ─────────────────────────────────────────────────────────────────────
def test_legacy_fb_caches_absent_from_active_cascade_rankings():
    """Ningún ranking activo declara ``api_football``/``fb_*`` como fuente."""
    from services.football_source_cascade import DEFAULT_RANKINGS

    forbidden = {"api_football", "fb_team_stats", "fb_standings",
                  "fb_h2h", "fb_injuries", "fb_fixture_stats", "fb_odds_cache"}
    for metric, ranking in DEFAULT_RANKINGS.items():
        for src in ranking:
            assert src not in forbidden, (
                f"F99.2: el cascade NO debe incluir '{src}' en el ranking "
                f"de '{metric}' (se considera fuente legacy decomisionada)."
            )


# ─────────────────────────────────────────────────────────────────────
# 4. No se escribe nueva provenance api_football
# ─────────────────────────────────────────────────────────────────────
def test_discovery_audit_marks_api_football_as_deprecated_stub():
    """En el discovery aggregator, el bucket ``api_football`` debe quedar
    declarado como ``API_FOOTBALL_DEPRECATED_STUB_USED`` (no como ganador)."""
    src = _DATA_INGESTION_PATH.read_text(encoding="utf-8")
    # El reason code debe estar presente — buena señal de telemetría.
    assert "API_FOOTBALL_DEPRECATED_STUB_USED" in src
    # El antiguo branch que asignaba ``primary_winner = "api_football"``
    # debe haber sido eliminado.
    assert 'primary_winner"] = "api_football"' not in src
    assert "_discovery_source\"] = \"api_football\"" not in src


# ─────────────────────────────────────────────────────────────────────
# 5. Fallo de TheSportsDB NO reactiva API-Sports
# ─────────────────────────────────────────────────────────────────────
def test_thesportsdb_failure_does_not_reactivate_api_sports():
    """Buscamos en el código que el branch antiguo ``af.fixtures_by_date``
    haya sido removido del fallback de discovery cuando TheSportsDB falla."""
    src = _DATA_INGESTION_PATH.read_text(encoding="utf-8")
    # Old branch keywords gone.
    assert "invoking API-Sports fallback" not in src
    # New telemetry codes present.
    assert "FOOTBALL_DISCOVERY_PARTIAL"        in src
    assert "THESPORTSDB_DISCOVERY_FAILED"      in src


# ─────────────────────────────────────────────────────────────────────
# 6. Documentación: el módulo lleva el aviso DEPRECATED STUB (F99.2)
# ─────────────────────────────────────────────────────────────────────
def test_api_football_module_is_marked_as_deprecated_stub():
    src = _API_FOOTBALL_PATH.read_text(encoding="utf-8")
    assert "DEPRECATED STUB" in src
    assert af.DEPRECATED_STUB_REASON_CODE == "API_FOOTBALL_DEPRECATED_STUB_USED"
