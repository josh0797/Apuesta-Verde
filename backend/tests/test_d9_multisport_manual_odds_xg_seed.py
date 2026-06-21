"""Sprint-D9 · Tests para los fixes de cascada multi-sport, manual odds y xG offline seed."""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest

from services import multi_sport_ingestion as m
from services import manual_odds_service as mos
from services import football_xg_offline_seed as xseed


# ============================================================
# Fix 1A — Multi-sport cascade upcoming-status filter
# ============================================================

class TestUpcomingStatusFilter:
    """``_is_upcoming_status`` debe descartar FT/IN5/Q3/etc para MLB/NBA."""

    def test_football_only_ns_tbd_pass(self):
        assert m._is_upcoming_status("football", "NS") is True
        assert m._is_upcoming_status("football", "TBD") is True
        assert m._is_upcoming_status("football", "FT") is False
        assert m._is_upcoming_status("football", "1H") is False

    def test_baseball_filters_innings_and_finished(self):
        assert m._is_upcoming_status("baseball", "NS") is True
        assert m._is_upcoming_status("baseball", "IN5") is False
        assert m._is_upcoming_status("baseball", "FT") is False
        assert m._is_upcoming_status("baseball", "AOT") is False

    def test_basketball_filters_quarters_and_finished(self):
        assert m._is_upcoming_status("basketball", "NS") is True
        assert m._is_upcoming_status("basketball", "Q3") is False
        assert m._is_upcoming_status("basketball", "FT") is False
        assert m._is_upcoming_status("basketball", "OT") is False

    def test_none_or_empty_is_treated_as_upcoming(self):
        # Upstream sometimes omits status — we tolerate it (the enrichment
        # layer drops empty payloads later).
        assert m._is_upcoming_status("baseball", None) is True
        assert m._is_upcoming_status("basketball", "") is False  # empty string ≠ None


@pytest.mark.asyncio
async def test_ingest_upcoming_multi_filters_non_upcoming(monkeypatch):
    """Si API-Sports devuelve un MIX (NS + FT), el filtro deja solo los NS."""
    fake_upcoming = [
        # 2 partidos NS (upcoming legítimos)
        {"id": 1, "league": {"id": 1, "name": "MLB"}, "status": {"short": "NS"},
         "teams": {"home": {"id": 1, "name": "Yankees"}, "away": {"id": 2, "name": "Boston"}},
         "timestamp": 1700000000},
        {"id": 2, "league": {"id": 1, "name": "MLB"}, "status": {"short": "NS"},
         "teams": {"home": {"id": 3, "name": "Mets"}, "away": {"id": 4, "name": "Cubs"}},
         "timestamp": 1700001000},
        # 1 partido FT (NO debe pasar)
        {"id": 3, "league": {"id": 1, "name": "MLB"}, "status": {"short": "FT"},
         "teams": {"home": {"id": 5, "name": "Dodgers"}, "away": {"id": 6, "name": "Giants"}},
         "timestamp": 1699990000},
        # 1 partido en IN5 (NO debe pasar)
        {"id": 4, "league": {"id": 1, "name": "MLB"}, "status": {"short": "IN5"},
         "teams": {"home": {"id": 7, "name": "Tigers"}, "away": {"id": 8, "name": "Astros"}},
         "timestamp": 1699995000},
    ]

    from services import api_sports as aps
    monkeypatch.setattr(aps, "fixtures_next_48h", AsyncMock(return_value=fake_upcoming))
    monkeypatch.setattr(aps, "top_leagues", lambda sport: {1})
    # enrich_multi devuelve un doc trivial
    monkeypatch.setattr(m, "enrich_multi",
                         AsyncMock(side_effect=lambda *a, **kw: {"match_id": a[3]["id"]}))

    import httpx
    async with httpx.AsyncClient() as client:
        out = await m.ingest_upcoming_multi("baseball", client, db=None, max_total=10)

    # Sólo los 2 NS pasan → 2 docs producidos
    assert len(out) == 2
    audit = m.LAST_MULTISPORT_INGEST_AUDIT["baseball"]
    assert audit["raw_count"] == 4
    assert audit["kept_after_filter"] == 2
    assert audit["filtered_not_upcoming"] == 2
    # Los rechazados aparecen en sample_dropped
    statuses_dropped = {s["status"] for s in audit["sample_dropped"]}
    assert statuses_dropped == {"FT", "IN5"}


# ============================================================
# Fix 1C — Manual odds service
# ============================================================

class TestManualOddsService:

    def test_compute_edge_with_valid_inputs(self):
        d = mos.compute_edge_from_odds(2.00, estimated_prob=0.55)
        assert d["implied_probability"] == 0.5
        assert d["edge"] == 0.05
        assert d["net_profit_if_win"] == 1.0

    def test_compute_edge_without_estimate(self):
        d = mos.compute_edge_from_odds(2.00, estimated_prob=None)
        assert d["implied_probability"] == 0.5
        assert d["edge"] is None

    def test_compute_edge_rejects_invalid_odds(self):
        # ≤ 1.01 returns null implied
        d = mos.compute_edge_from_odds(0.5, estimated_prob=0.5)
        assert d["implied_probability"] is None

    def test_request_validates_decimal_odds_lower_bound(self):
        with pytest.raises(Exception):
            mos.ManualOddsRequest(
                match_id="x", sport="football", market_key="ml", decimal_odds=1.01,
            )

    def test_request_normalizes_sport_and_market_key(self):
        req = mos.ManualOddsRequest(
            match_id="x", sport="SOCCER", market_key="Over 2.5",
            decimal_odds=1.95, estimated_prob=0.6,
        )
        # soccer → football, market_key lowered + spaces→underscores
        assert req.sport == "football"
        assert req.market_key == "over_2.5"

    def test_request_rejects_unknown_sport(self):
        with pytest.raises(Exception):
            mos.ManualOddsRequest(
                match_id="x", sport="curling", market_key="ml", decimal_odds=2.0,
            )


@pytest.mark.asyncio
async def test_persist_manual_odds_inserts_and_get_returns_latest():
    """Smoke test contra mongomock — verifica el round-trip persistencia."""
    pytest.importorskip("mongomock_motor")
    from mongomock_motor import AsyncMongoMockClient

    db = AsyncMongoMockClient()["test"]
    req = mos.ManualOddsRequest(
        match_id="abc-123", sport="baseball", market_key="moneyline_home",
        decimal_odds=1.85, estimated_prob=0.62,
    )
    doc = await mos.persist_manual_odds(db, req)
    assert doc["decimal_odds"] == 1.85
    assert doc["edge"] == pytest.approx(0.62 - 1 / 1.85, abs=1e-6)

    fetched = await mos.get_latest_manual_odds(db, "abc-123")
    assert fetched is not None
    assert fetched["decimal_odds"] == 1.85

    # Un segundo insert devuelve el más reciente
    req2 = mos.ManualOddsRequest(
        match_id="abc-123", sport="baseball", market_key="moneyline_home",
        decimal_odds=2.10, estimated_prob=0.55,
    )
    await mos.persist_manual_odds(db, req2)
    fetched2 = await mos.get_latest_manual_odds(db, "abc-123")
    assert fetched2["decimal_odds"] == 2.10


# ============================================================
# Fix 2B — xG offline seed
# ============================================================

class TestXgOfflineSeedHelpers:

    def test_norm_team_strips_accents(self):
        assert xseed._norm_team("Atlético Madrid") == "atletico madrid"
        assert xseed._norm_team("Mönchengladbach") == "monchengladbach"
        assert xseed._norm_team("  Real Madrid  ") == "real madrid"
        assert xseed._norm_team(None) == ""

    def test_canonicalize_aliases(self):
        assert xseed._canonicalize_team("Manchester City") == "man city"
        assert xseed._canonicalize_team("Atlético Madrid") == "ath madrid"
        assert xseed._canonicalize_team("Real Sociedad") == "sociedad"
        assert xseed._canonicalize_team("Internazionale") == "inter"
        # Sin alias → devuelve el norm
        assert xseed._canonicalize_team("Unknown Team") == "unknown team"

    def test_build_seed_from_dataset_returns_empty_when_missing(self, tmp_path):
        out = xseed.build_seed_from_dataset(tmp_path / "no-such-file.json")
        assert out == []

    def test_build_seed_groups_by_team_and_league(self, tmp_path):
        """Un partido genera 2 entradas (home + away)."""
        ds = tmp_path / "fake.json"
        import json
        ds.write_text(json.dumps([
            {"date": "2022-01-01", "league": "EPL", "season": "2122",
             "home_team": "Man City", "away_team": "Liverpool",
             "xg_h": 2.3, "xg_a": 1.1, "home_goals": 3, "away_goals": 1},
            {"date": "2022-01-08", "league": "EPL", "season": "2122",
             "home_team": "Arsenal", "away_team": "Man City",
             "xg_h": 0.9, "xg_a": 2.5, "home_goals": 0, "away_goals": 2},
        ]))
        docs = xseed.build_seed_from_dataset(ds)
        # 3 equipos en EPL: Man City (2 matches), Liverpool, Arsenal
        teams = {(d["team_norm"], d["league"]): d for d in docs}
        assert ("man city", "EPL") in teams
        assert teams[("man city", "EPL")]["matches_count"] == 2
        # Man City como home en 1er match → xg_for=2.3
        # Man City como away en 2do match → xg_for=2.5
        xgs = sorted(m["xg_for"] for m in teams[("man city", "EPL")]["matches"])
        assert xgs == [2.3, 2.5]


@pytest.mark.asyncio
async def test_offline_seed_lookup_alias_works():
    """E2E con mongomock — persist + lookup con aliases."""
    pytest.importorskip("mongomock_motor")
    from mongomock_motor import AsyncMongoMockClient

    db = AsyncMongoMockClient()["test"]
    docs = [{
        "team_norm": "man city", "team_name": "Man City", "league": "EPL",
        "matches": [
            {"date": "2022-01-01", "opponent": "X", "xg_for": 2.3, "xg_against": 1.1,
              "goals_for": 3, "goals_against": 1, "venue": "home", "season": "2122"},
        ],
        "matches_count": 1,
        "seeded_at": datetime.now(timezone.utc),
        "source": "test",
    }, {
        "team_norm": "ath madrid", "team_name": "Ath Madrid", "league": "LaLiga",
        "matches": [
            {"date": "2022-02-01", "opponent": "Y", "xg_for": 1.5, "xg_against": 0.8,
              "goals_for": 2, "goals_against": 0, "venue": "home", "season": "2122"},
        ],
        "matches_count": 1,
        "seeded_at": datetime.now(timezone.utc),
        "source": "test",
    }]
    await xseed.persist_seed(db, docs)

    # Lookup por alias: "Manchester City" → "man city"
    res1 = await xseed.get_offline_xg_history(db, "Manchester City")
    assert res1 is not None
    assert res1["matches_count"] == 1
    assert res1["league"] == "EPL"

    # Lookup por nombre con tilde: "Atlético Madrid" → "ath madrid"
    res2 = await xseed.get_offline_xg_history(db, "Atlético Madrid", league="LaLiga")
    assert res2 is not None
    assert res2["league"] == "LaLiga"
    assert res2["reason_code"] == "XG_OFFLINE_SEED_HIT"

    # Equipo no encontrado
    res3 = await xseed.get_offline_xg_history(db, "Unknown Team")
    assert res3 is None
