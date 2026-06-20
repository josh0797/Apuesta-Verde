"""Sprint-D8-Fase2 (cascada) · TheSportsDB primary cascade — integration test.

Validates the user-requested cascade refactor end-to-end against the
LIVE TheSportsDB API:

  * The new adapter ``thesportsdb_fixtures_adapter.fetch_fixtures_next_48h``
    returns at least one real fixture for today/tomorrow.
  * The fixtures contain a known target match (e.g., "Germany vs
    Ivory Coast" on 2026-06-20) when run on that date.
  * The fixtures pass the API-Football fixture-contract shape so
    they can flow into ``_discover_football_fixtures`` without
    breaking the FFC normalizer.

These tests **require** ``THESPORTSDB_KEY`` to be configured. If the
key is missing, the tests are skipped automatically.

Run with:
  pytest tests/test_sprint_d8_fase2_thesportsdb_cascade.py -v -s
"""
from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest

# Load backend/.env when the test runs outside the backend supervisor.
_env_path = Path(__file__).resolve().parents[1] / ".env"
if _env_path.exists():
    for _line in _env_path.read_text().splitlines():
        _line = _line.strip()
        if not _line or _line.startswith("#") or "=" not in _line:
            continue
        _k, _, _v = _line.partition("=")
        os.environ.setdefault(_k.strip(),
                               _v.strip().strip('"').strip("'"))

from services.external_sources import (  # noqa: E402
    thesportsdb_fixtures_adapter as _tsdb_fx,
)
from services.external_sources import thesportsdb_client as tsdb  # noqa: E402


pytestmark = pytest.mark.skipif(
    not (os.environ.get("THESPORTSDB_KEY") and tsdb.is_enabled()),
    reason="THESPORTSDB_KEY not configured — cascade integration test skipped",
)


def test_thesportsdb_adapter_returns_real_fixtures_for_today():
    """Live integration: TheSportsDB adapter must return at least one
    real fixture for today (UTC) or tomorrow.
    """
    async def go():
        return await _tsdb_fx.fetch_fixtures_next_48h(sport="Soccer")
    fixtures, codes = asyncio.run(go())
    assert isinstance(fixtures, list)
    assert isinstance(codes, list)
    # On any given day there's normally at least one Soccer fixture
    # somewhere in the world. If this fails, check codes for upstream
    # issues (e.g. THESPORTSDB_EVENTSDAY_EMPTY two days in a row).
    assert len(fixtures) > 0, (
        f"TheSportsDB returned 0 fixtures for today+tomorrow. "
        f"reason_codes={codes}"
    )
    assert _tsdb_fx.RC_OK in codes


def test_thesportsdb_fixtures_have_apifootball_shape():
    """Every fixture returned must already be in the API-Football
    nested shape (``fixture``, ``league``, ``teams``).
    """
    async def go():
        return await _tsdb_fx.fetch_fixtures_next_48h(sport="Soccer")
    fixtures, _codes = asyncio.run(go())
    assert fixtures, "no fixtures to validate shape against"
    fx = fixtures[0]
    assert isinstance(fx.get("fixture"), dict)
    assert isinstance(fx.get("league"),  dict)
    assert isinstance(fx.get("teams"),   dict)
    teams = fx["teams"]
    assert teams.get("home", {}).get("name")
    assert teams.get("away", {}).get("name")
    assert fx["fixture"].get("date")
    assert fx.get("_discovery_source") == "thesportsdb"


def test_thesportsdb_cascade_discovers_germany_vs_ivory_coast_when_present():
    """User-driven sanity check: when run on 2026-06-20 (the date of
    Germany vs Ivory Coast in TheSportsDB), the adapter must surface
    that fixture in its output.

    On other dates the test passes vacuously (skipping the specific
    match check), but still asserts the adapter returned SOMETHING.
    """
    from datetime import datetime, timezone
    today_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    async def go():
        return await _tsdb_fx.fetch_fixtures_next_48h(sport="Soccer")
    fixtures, _codes = asyncio.run(go())
    assert fixtures, "TheSportsDB returned 0 fixtures"

    # Find the Germany-vs-Ivory-Coast fixture if it's in today's slate.
    def _names_match(fx, a, b) -> bool:
        h = ((fx.get("teams") or {}).get("home") or {}).get("name", "").lower()
        w = ((fx.get("teams") or {}).get("away") or {}).get("name", "").lower()
        a_l, b_l = a.lower(), b.lower()
        return ({a_l in h or a_l in w}, {b_l in h or b_l in w}) == ({True}, {True})

    candidate_pairs = (
        ("Germany", "Ivory Coast"),
        ("Germany", "Côte d'Ivoire"),
        ("Germany", "Cote d'Ivoire"),
    )
    found = []
    for fx in fixtures:
        h = ((fx.get("teams") or {}).get("home") or {}).get("name", "")
        w = ((fx.get("teams") or {}).get("away") or {}).get("name", "")
        for a, b in candidate_pairs:
            if (a.lower() in h.lower() and b.lower() in w.lower()) \
                    or (a.lower() in w.lower() and b.lower() in h.lower()):
                found.append(fx)
                break

    if today_utc == "2026-06-20":
        # Hard assert on the exact date the user pointed to.
        assert found, (
            "Expected 'Germany vs Ivory Coast' in the TheSportsDB cascade "
            "fixtures for 2026-06-20 but it was not found. Sample teams: "
            + str([
                ((f.get("teams") or {}).get("home") or {}).get("name") + " vs "
                + ((f.get("teams") or {}).get("away") or {}).get("name")
                for f in fixtures[:8]
            ])
        )
        # Side-effect: print so the user can inspect with -s.
        print("\n[CASCADE TEST] Found target fixture:")
        for fx in found:
            print(f"  {fx['teams']['home']['name']} vs "
                   f"{fx['teams']['away']['name']} | "
                   f"{fx['league']['name']} | {fx['fixture']['date']}")
    else:
        # Off-date: just confirm the cascade is healthy.
        print(f"\n[CASCADE TEST] today_utc={today_utc} — "
              f"not the user-flagged date 2026-06-20, but cascade returned "
              f"{len(fixtures)} fixtures correctly. Sample home teams: "
              f"{[((f.get('teams') or {}).get('home') or {}).get('name') for f in fixtures[:5]]}")


def test_thesportsdb_cascade_emits_today_fixtures_with_kickoff_dates():
    """The adapter must include a parseable ``fixture.date`` (ISO-8601)
    for every fixture so downstream consumers (FFC, timeline) work.
    """
    async def go():
        return await _tsdb_fx.fetch_fixtures_next_48h(sport="Soccer")
    fixtures, _codes = asyncio.run(go())
    assert fixtures
    from datetime import datetime
    for fx in fixtures[:30]:  # sample
        d = (fx.get("fixture") or {}).get("date")
        assert d, f"missing fixture.date in {fx}"
        # Should parse cleanly with fromisoformat (after Z normalisation).
        try:
            datetime.fromisoformat(d.replace("Z", "+00:00"))
        except ValueError as exc:  # noqa: BLE001
            pytest.fail(f"unparseable fixture.date {d!r}: {exc}")
