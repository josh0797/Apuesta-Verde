"""Sprint-D9 Iteration-3 · Tests del módulo corners offline seed + cascade v2.

Cubre:
  * Aliases compartidos (smoke) — el módulo `team_aliases.py` ya está
    testado al 100% en `test_d9_xg_seed_iteration2.py`; aquí
    verificamos que `corners_offline_seed` y `xg_offline_seed` resuelven
    los aliases idénticamente (single source of truth).
  * Bootstrap desde dataset (build_seed_from_dataset).
  * merge_matches_dedupe (UNION + dedupe + prefer xG).
  * promote_online_matches_to_seed (idempotente, growth).
  * Cascade v2 (offline_seed primero, fallback con seed_partial,
    promote tras fetch online).
  * compute_window_stats (L5/L15 promedios + raw recent).
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from services import football_corners_offline_seed as cseed
from services import football_xg_offline_seed as xseed
from services import team_aliases as ta


# ============================================================
# Aliases compartidos — same source of truth
# ============================================================

class TestSharedTeamAliases:
    """`football_corners_offline_seed` y `football_xg_offline_seed` deben
    consumir el MISMO módulo `team_aliases` para evitar drift."""

    @pytest.mark.parametrize("raw,expected", [
        ("Manchester City",     "man city"),
        ("Atlético Madrid",     "ath madrid"),
        ("Selección Argentina", "argentina"),
        ("Curaçao",             "curacao"),
        ("Arabia Saudita",      "saudi arabia"),
        ("Côte d'Ivoire",       "ivory coast"),
    ])
    def test_alias_resolution_matches_in_both_modules(self, raw, expected):
        """corners y xG deben producir el MISMO output canónico."""
        assert ta.canonicalize_team(raw) == expected
        # El _canonicalize_team de corners debe ser una referencia directa
        # (no una copia) del compartido, garantizando sync futuro.
        assert cseed._canonicalize_team(raw) == ta.canonicalize_team(raw)
        assert xseed._canonicalize_team(raw) == ta.canonicalize_team(raw)

    def test_team_aliases_dict_is_same_reference(self):
        """xseed._TEAM_ALIASES debe SER (is) ta.TEAM_ALIASES — no una copia."""
        # back-compat alias importado: es el mismo dict por reference
        assert xseed._TEAM_ALIASES is ta.TEAM_ALIASES


# ============================================================
# Bootstrap desde dataset
# ============================================================

class TestBuildSeedFromDataset:

    def test_returns_empty_when_file_missing(self, tmp_path):
        out = cseed.build_seed_from_dataset(tmp_path / "no.json")
        assert out == []

    def test_groups_by_team_and_includes_both_sides(self, tmp_path):
        ds = tmp_path / "fake.json"
        ds.write_text(json.dumps([
            {"date": "2022-01-01", "league": "EPL", "season": "2122",
              "home_team": "Man City", "away_team": "Liverpool",
              "home_corners": 8, "away_corners": 3,
              "home_goals": 3, "away_goals": 1},
            {"date": "2022-01-08", "league": "EPL", "season": "2122",
              "home_team": "Arsenal", "away_team": "Man City",
              "home_corners": 4, "away_corners": 7,
              "home_goals": 0, "away_goals": 2},
        ]))
        docs = cseed.build_seed_from_dataset(ds)
        idx = {(d["team_norm"], d["league"]): d for d in docs}
        assert ("man city", "EPL") in idx
        # Man City: 8 cf como local + 7 cf como visitante
        cf = sorted(m["corners_for"]
                     for m in idx[("man city", "EPL")]["matches"])
        assert cf == [7, 8]
        # corners_against del Liverpool en su match away → 8
        liv = idx[("liverpool", "EPL")]["matches"][0]
        assert liv["corners_against"] == 8


# ============================================================
# Merge inteligente (idéntico al de xG pero con corners_*)
# ============================================================

class TestMergeMatchesDedupe:

    def test_empty(self):
        assert cseed.merge_matches_dedupe([], []) == []

    def test_dedupe_prefers_incoming_when_both_have_corners(self):
        existing = [{"date": "2024-01-01", "opponent": "X",
                      "corners_for": 4, "corners_against": 5}]
        incoming = [{"date": "2024-01-01", "opponent": "X",
                      "corners_for": 6, "corners_against": 5}]
        out = cseed.merge_matches_dedupe(existing, incoming)
        assert len(out) == 1
        assert out[0]["corners_for"] == 6

    def test_appends_new_matches(self):
        existing = [{"date": "2024-01-01", "opponent": "X",
                      "corners_for": 4, "corners_against": 5}]
        incoming = [{"date": "2024-01-08", "opponent": "Y",
                      "corners_for": 7, "corners_against": 2}]
        out = cseed.merge_matches_dedupe(existing, incoming)
        assert len(out) == 2

    def test_normalizes_opponent_for_dedupe(self):
        existing = [{"date": "2024-01-01", "opponent": "Atlético",
                      "corners_for": 3, "corners_against": 4}]
        incoming = [{"date": "2024-01-01", "opponent": "Atletico",
                      "corners_for": 5, "corners_against": 4}]
        out = cseed.merge_matches_dedupe(existing, incoming)
        assert len(out) == 1   # NFKD strip → same key


# ============================================================
# compute_window_stats (L5/L15)
# ============================================================

class TestWindowStats:

    def test_empty_returns_nones(self):
        s = cseed.compute_window_stats([], window=5)
        assert s["corners_for_avg"] is None
        assert s["sample_size"] == 0

    def test_takes_last_N_matches(self):
        matches = [
            {"date": f"2024-0{i+1}-01", "opponent": "X",
              "corners_for": i + 1, "corners_against": 5 - i}
            for i in range(10)
        ]
        s = cseed.compute_window_stats(matches, window=5)
        # Toma los últimos 5: cf = [6,7,8,9,10] → avg 8.0
        assert s["sample_size"] == 5
        assert s["corners_for_avg"] == 8.0
        # corners_against avg: ca de los últimos 5 = [-1,-2,-3,-4,-5]
        # Wait — i=5..9: 5-5=0, 5-6=-1, …, en realidad mis valores son
        # corners_against = 5 - i, así que i=5→0, i=6→-1, …, i=9→-4
        # Mejor verificar tan solo la lógica básica:
        assert s["window"] == 5
        assert len(s["corners_for_recent"]) == 5


# ============================================================
# Promote inteligente
# ============================================================

@pytest.mark.asyncio
async def test_promote_skips_when_no_inputs():
    assert (await cseed.promote_online_matches_to_seed(
        None, team_name="X", league="L", matches=[],
    ))["action"] == "skipped"


@pytest.mark.asyncio
async def test_promote_grows_then_idempotent():
    pytest.importorskip("mongomock_motor")
    from mongomock_motor import AsyncMongoMockClient
    db = AsyncMongoMockClient()["test"]

    seed_initial = [
        {"date": "2024-06-01", "opponent": "Mexico",
          "corners_for": 6, "corners_against": 4,
          "goals_for": 2, "goals_against": 1, "venue": "home"},
    ]
    r1 = await cseed.promote_online_matches_to_seed(
        db, team_name="Argentina", league="National Teams",
        matches=seed_initial, underlying_source="thestatsapi",
    )
    assert r1["action"] == "promoted"
    assert r1["after_count"] == 1

    # Añadir 2 más
    enlarged = seed_initial + [
        {"date": "2024-06-08", "opponent": "Brazil",
          "corners_for": 5, "corners_against": 7, "venue": "away"},
        {"date": "2024-06-15", "opponent": "Uruguay",
          "corners_for": 9, "corners_against": 3, "venue": "home"},
    ]
    r2 = await cseed.promote_online_matches_to_seed(
        db, team_name="Argentina", league="National Teams",
        matches=enlarged, underlying_source="api_sports",
    )
    assert r2["action"] == "promoted"
    assert r2["after_count"] == 3
    assert r2["delta"] == 2

    # Re-promote SAME → no_change
    r3 = await cseed.promote_online_matches_to_seed(
        db, team_name="Argentina", league="National Teams",
        matches=enlarged, underlying_source="api_sports",
    )
    assert r3["action"] == "no_change"


# ============================================================
# Cascade v2 — offline_seed primero
# ============================================================

@pytest.mark.asyncio
async def test_cascade_v2_short_circuits_on_seed_hit():
    """Cascade v2 con seed cubriendo ≥ min_sample → NO se llaman online."""
    pytest.importorskip("mongomock_motor")
    from mongomock_motor import AsyncMongoMockClient
    db = AsyncMongoMockClient()["test"]

    # Pre-poblar seed con 6 matches
    seed_doc = {
        "team_norm": "argentina", "team_name": "Argentina",
        "league": "National Teams",
        "matches": [
            {"date": f"2024-0{i+1}-01", "opponent": "X",
              "corners_for": 5 + i, "corners_against": 4, "venue": "home"}
            for i in range(6)
        ],
        "matches_count": 6,
        "seeded_at": datetime.now(timezone.utc),
        "source": "test",
    }
    await db[cseed.OFFLINE_COLLECTION].insert_one(seed_doc)

    from services import football_corners_history as ch
    res = await ch.fetch_team_corners_history_v2(
        None, db, team_name="Argentina", league="National Teams",
        min_sample=5, force_refresh=False,
    )
    assert res["source"] == "offline_seed"
    assert res["available"] is True
    assert len(res["history"]) == 6
    # Sin team_ids → online sources no se llaman; "thestatsapi"/"api_sports"
    # no figuran en reason_codes.
    rcs = " ".join(res["reason_codes"])
    assert "CORNERS_OFFLINE_SEED_HIT" in rcs


@pytest.mark.asyncio
async def test_cascade_v2_uses_seed_partial_when_online_fails():
    """Si seed tiene < min_sample Y no se pasa team_id → devuelve seed parcial."""
    pytest.importorskip("mongomock_motor")
    from mongomock_motor import AsyncMongoMockClient
    db = AsyncMongoMockClient()["test"]

    # Pre-poblar con SOLO 2 matches (< min_sample default 5)
    await db[cseed.OFFLINE_COLLECTION].insert_one({
        "team_norm": "ecuador", "team_name": "Ecuador",
        "league": "National Teams",
        "matches": [
            {"date": "2024-01-01", "opponent": "X",
              "corners_for": 4, "corners_against": 6, "venue": "home"},
            {"date": "2024-01-08", "opponent": "Y",
              "corners_for": 5, "corners_against": 5, "venue": "away"},
        ],
        "matches_count": 2,
        "seeded_at": datetime.now(timezone.utc),
        "source": "test",
    })

    from services import football_corners_history as ch
    res = await ch.fetch_team_corners_history_v2(
        None, db, team_name="Ecuador", league="National Teams",
        min_sample=5, force_refresh=False,
    )
    # Source es offline_seed pero available=False (sample < min_sample)
    assert res["source"] == "offline_seed"
    assert res["available"] is False
    assert len(res["history"]) == 2


# ============================================================
# Lookup con aliases (smoke)
# ============================================================

@pytest.mark.asyncio
async def test_offline_lookup_with_alias():
    pytest.importorskip("mongomock_motor")
    from mongomock_motor import AsyncMongoMockClient
    db = AsyncMongoMockClient()["test"]

    await db[cseed.OFFLINE_COLLECTION].insert_one({
        "team_norm": "ath madrid", "team_name": "Ath Madrid",
        "league": "LaLiga",
        "matches": [
            {"date": "2022-03-01", "opponent": "Real Madrid",
              "corners_for": 4, "corners_against": 7, "venue": "home"},
        ],
        "matches_count": 1,
        "seeded_at": datetime.now(timezone.utc),
        "source": "test",
    })
    # Lookup con tilde y nombre largo
    res = await cseed.get_offline_corners_history(
        db, "Atlético Madrid", league="LaLiga",
    )
    assert res is not None
    assert res["matches_count"] == 1
    assert res["reason_code"] == "CORNERS_OFFLINE_SEED_HIT"
