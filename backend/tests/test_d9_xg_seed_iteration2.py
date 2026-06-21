"""Sprint-D9 Iteration-2 · Tests aliases nacionales + cascade reordenado + merge promote."""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest

from services import football_xg_offline_seed as xseed


# ============================================================
# Fix 1a — Aliases nacionales
# ============================================================

class TestNationalTeamAliases:
    """_canonicalize_team debe mapear nombres ES↔EN de selecciones."""

    @pytest.mark.parametrize("raw,expected", [
        ("España",             "spain"),
        ("Selección de España", "spain"),
        ("Países Bajos",       "netherlands"),
        ("Holanda",            "netherlands"),
        ("Bélgica",            "belgium"),
        ("Croacia",            "croatia"),
        ("Turquía",            "turkey"),
        ("Türkiye",            "turkey"),
        ("Polonia",            "poland"),
        ("Suecia",             "sweden"),
        ("República Checa",    "czech republic"),
        ("Czechia",            "czech republic"),
        ("Hungría",            "hungary"),
        ("Magyarország",       "hungary"),
        # Sudamérica
        ("Brasil",             "brazil"),
        ("Perú",               "peru"),
        ("Selección Argentina", "argentina"),
        # CONCACAF — caso del bug del usuario
        ("Curaçao",            "curacao"),
        ("México",             "mexico"),
        ("Estados Unidos",     "usa"),
        ("United States",      "usa"),
        # África
        ("Marruecos",          "morocco"),
        ("Túnez",              "tunisia"),
        ("Camerún",            "cameroon"),
        ("Côte d'Ivoire",      "ivory coast"),
        ("Sudáfrica",          "south africa"),
        # AFC
        ("Arabia Saudita",     "saudi arabia"),
        ("Japón",              "japan"),
        ("Corea del Sur",      "south korea"),
        ("Korea Republic",     "south korea"),
        ("Irán",               "iran"),
        # OFC / misc
        ("Nueva Zelanda",      "new zealand"),
        ("Cabo Verde",         "cape verde"),
    ])
    def test_national_team_alias_maps(self, raw, expected):
        assert xseed._canonicalize_team(raw) == expected


# ============================================================
# Fix 2b — Merge inteligente (UNION + dedupe + max conteo)
# ============================================================

class TestMergeMatchesDedupe:

    def test_empty_inputs(self):
        assert xseed.merge_matches_dedupe([], []) == []
        m = [{"date": "2024-01-01", "opponent": "X", "xg_for": 1.0}]
        assert xseed.merge_matches_dedupe(m, []) == m
        assert xseed.merge_matches_dedupe([], m) == m

    def test_dedupe_by_date_and_opponent(self):
        existing = [{"date": "2024-01-01", "opponent": "X", "xg_for": 1.0}]
        incoming = [{"date": "2024-01-01", "opponent": "X", "xg_for": 1.2}]
        out = xseed.merge_matches_dedupe(existing, incoming)
        assert len(out) == 1
        # Incoming gana cuando ambos tienen xG (más fresco)
        assert out[0]["xg_for"] == 1.2

    def test_union_adds_new_matches(self):
        existing = [
            {"date": "2024-01-01", "opponent": "X", "xg_for": 1.0},
        ]
        incoming = [
            {"date": "2024-01-08", "opponent": "Y", "xg_for": 2.0},
            {"date": "2024-01-15", "opponent": "Z", "xg_for": 0.8},
        ]
        out = xseed.merge_matches_dedupe(existing, incoming)
        assert len(out) == 3
        # Sorted asc by date
        assert out[0]["date"] == "2024-01-01"
        assert out[-1]["date"] == "2024-01-15"

    def test_prefer_xg_over_missing(self):
        """Si existing tiene xG=None y incoming tiene xG numérico, gana incoming."""
        existing = [{"date": "2024-01-01", "opponent": "X",
                      "xg_for": None, "xg_against": None}]
        incoming = [{"date": "2024-01-01", "opponent": "X",
                      "xg_for": 1.5, "xg_against": 0.8}]
        out = xseed.merge_matches_dedupe(existing, incoming)
        assert out[0]["xg_for"] == 1.5

    def test_opponent_normalized_for_dedupe(self):
        """'ManCity' y 'Manchester City' generan la misma key tras aliases? NO —
        el dedupe normaliza pero NO aplica aliases. Verifica que la key
        usa _norm_team (NFKD strip + lowercase)."""
        existing = [{"date": "2024-01-01", "opponent": "Atlético", "xg_for": 1.0}]
        incoming = [{"date": "2024-01-01", "opponent": "Atletico", "xg_for": 1.5}]
        out = xseed.merge_matches_dedupe(existing, incoming)
        # NFKD strip hace que ambos colapsen → 1 solo match
        assert len(out) == 1


# ============================================================
# Fix 2b — promote_online_matches_to_seed
# ============================================================

@pytest.mark.asyncio
async def test_promote_skips_when_empty():
    res = await xseed.promote_online_matches_to_seed(
        None, team_name="Argentina", league="WC", matches=[],
    )
    assert res["action"] == "skipped"


@pytest.mark.asyncio
async def test_promote_grows_seed_then_idempotent():
    """E2E con mongomock: promote crece, re-promote = no_change."""
    pytest.importorskip("mongomock_motor")
    from mongomock_motor import AsyncMongoMockClient

    db = AsyncMongoMockClient()["test"]
    initial = [
        {"date": "2024-06-01", "opponent": "Mexico", "xg_for": 1.5, "xg_against": 0.8,
          "goals_for": 2, "goals_against": 1, "venue": "home"},
    ]
    r1 = await xseed.promote_online_matches_to_seed(
        db, team_name="Argentina", league="National Teams",
        matches=initial, underlying_source="fbref",
    )
    assert r1["action"] == "promoted"
    assert r1["after_count"] == 1

    # Añadir 2 matches nuevos
    new_ones = initial + [
        {"date": "2024-06-08", "opponent": "Brazil", "xg_for": 2.1, "xg_against": 1.2,
          "goals_for": 1, "goals_against": 1, "venue": "away"},
        {"date": "2024-06-15", "opponent": "Uruguay", "xg_for": 1.9, "xg_against": 0.7,
          "goals_for": 2, "goals_against": 0, "venue": "home"},
    ]
    r2 = await xseed.promote_online_matches_to_seed(
        db, team_name="Argentina", league="National Teams",
        matches=new_ones, underlying_source="understat",
    )
    assert r2["action"] == "promoted"
    assert r2["after_count"] == 3
    assert r2["delta"] == 2

    # Re-promote SAME data → no_change
    r3 = await xseed.promote_online_matches_to_seed(
        db, team_name="Argentina", league="National Teams",
        matches=new_ones, underlying_source="understat",
    )
    assert r3["action"] == "no_change"


# ============================================================
# Fix 2a — Cascade reordenado: offline_seed primero
# ============================================================

@pytest.mark.asyncio
async def test_cascade_short_circuits_when_seed_has_coverage():
    """Si seed tiene ≥ min_samples y NO se pidió force_refresh, los sources
    online NUNCA se llaman."""
    pytest.importorskip("mongomock_motor")
    from mongomock_motor import AsyncMongoMockClient

    db = AsyncMongoMockClient()["test"]
    # Preseed Argentina con 6 matches
    seed_doc = {
        "team_norm": "argentina", "team_name": "Argentina",
        "league": "National Teams",
        "matches": [
            {"date": f"2024-0{i+1}-01", "opponent": "X",
              "xg_for": 1.5, "xg_against": 0.8, "venue": "home"}
            for i in range(6)
        ],
        "matches_count": 6,
        "seeded_at": datetime.now(timezone.utc),
        "source": "test",
    }
    await db[xseed.OFFLINE_COLLECTION].insert_one(seed_doc)

    # Verificar lookup directo
    res = await xseed.get_offline_xg_history(db, "Argentina", league="National Teams")
    assert res is not None and res["matches_count"] == 6


@pytest.mark.asyncio
async def test_cascade_uses_seed_partial_when_online_fails():
    """Cascade integrado: si seed tiene < min_samples Y online falla,
    devuelve el seed_partial con available=False (RC_INSUFFICIENT_SAMPLE)."""
    pytest.importorskip("mongomock_motor")
    from mongomock_motor import AsyncMongoMockClient

    db = AsyncMongoMockClient()["test"]
    # Preseed con SOLO 2 matches (menos del default min_samples=5)
    seed_doc = {
        "team_norm": "ecuador", "team_name": "Ecuador",
        "league": "National Teams",
        "matches": [
            {"date": "2024-01-01", "opponent": "X",
              "xg_for": 1.0, "xg_against": 0.5, "venue": "home"},
            {"date": "2024-01-08", "opponent": "Y",
              "xg_for": 1.2, "xg_against": 0.7, "venue": "away"},
        ],
        "matches_count": 2,
        "seeded_at": datetime.now(timezone.utc),
        "source": "test",
    }
    await db[xseed.OFFLINE_COLLECTION].insert_one(seed_doc)

    from services import football_xg_real_client as xc
    # Mock todas las sources a vacío
    async def empty_fetch(team_name, **kw): return []
    saved = (xc._fetch_understat, xc._fetch_fbref, xc._fetch_footystats, xc._fetch_thestatsapi)
    xc._fetch_understat = empty_fetch
    xc._fetch_fbref = empty_fetch
    xc._fetch_footystats = empty_fetch
    xc._fetch_thestatsapi = empty_fetch
    try:
        res = await xc.get_team_xg_history(
            "Ecuador", league="National Teams", db=db, force_refresh=True,
        )
    finally:
        xc._fetch_understat, xc._fetch_fbref, xc._fetch_footystats, xc._fetch_thestatsapi = saved

    # Source es offline_seed, pero available=False porque hay solo 2 matches
    assert res["source"] == "offline_seed"
    assert res["available"] is False  # 2 < min_samples=5
    assert "offline_seed" in res["tried_sources"]
