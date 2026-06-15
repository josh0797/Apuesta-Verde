"""F87 — Football fixture discovery cascade tests.

Validates:
  * F87.a — TheStatsAPI primary wins on ≥ 5 fixtures.
  * F87.a — empty TheStatsAPI falls through to API-Football.
  * F87.a — DISABLED flag skips TheStatsAPI entirely.
  * F87.b — Sofascore PW + scrape.do adapters normalise correctly.
  * F87.c — unknown competitions surface in a low-priority bucket
            (instead of silent discard) unless blocklisted.
  * Cascade merge — multiple <5 sources are merged + deduped.
"""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest

from services import data_ingestion as di, football_competitions as fc
from services.external_sources import (
    scrapedo_fixtures_adapter as sd_fx,
    sofascore_fixtures_adapter as so_fx,
    thestatsapi_fixtures_adapter as ts_fx,
)


# ─────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────
def _make_ts_fixture(idx: int, *, league: str = "Premier League",
                      home: str | None = None, away: str | None = None,
                      ts: int | None = None) -> dict:
    """Build a TheStatsAPI raw fixture dict suitable for ``_normalise_fixture``."""
    ts_val = ts if ts is not None else int(datetime.now(timezone.utc).timestamp()) + 3600 + idx * 60
    return {
        "id":          f"ts-{idx}",
        "timestamp":   ts_val,
        "status":      {"short": "scheduled"},
        "league":      {"id": 39, "name": league, "country": "England"},
        "teams": {
            "home": {"id": 100 + idx, "name": home or f"Home{idx}"},
            "away": {"id": 200 + idx, "name": away or f"Away{idx}"},
        },
    }


def _make_af_fixture(idx: int, *, league: str = "Premier League",
                      home: str | None = None, away: str | None = None,
                      ts: int | None = None) -> dict:
    """Build an API-Football "next-48h" fixture dict."""
    ts_val = ts if ts is not None else int(datetime.now(timezone.utc).timestamp()) + 3600 + idx * 60
    return {
        "id": f"af-{idx}",
        "fixture": {
            "id": f"af-{idx}", "timestamp": ts_val,
            "date":   datetime.fromtimestamp(ts_val, tz=timezone.utc).isoformat(),
            "status": {"short": "NS"},
        },
        "league": {"id": 39, "name": league, "country": "England"},
        "teams": {
            "home": {"id": 100 + idx, "name": home or f"Home{idx}"},
            "away": {"id": 200 + idx, "name": away or f"Away{idx}"},
        },
        "timestamp": ts_val,
        "status":    {"short": "NS"},
    }


# =====================================================================
# F87.a — TheStatsAPI primary
# =====================================================================
class TestF87aTheStatsAPIPrimary:
    @pytest.mark.asyncio
    async def test_thestatsapi_primary_wins_when_enough_fixtures(self, monkeypatch):
        monkeypatch.delenv("ENABLE_THESTATSAPI_FIXTURES_PRIMARY", raising=False)
        ts_fixtures = [
            {"id": f"ts-{i}", "_external_source": "thestatsapi",
             "fixture": {"id": f"ts-{i}",
                          "timestamp": int(datetime.now(timezone.utc).timestamp()) + 3600 + i,
                          "date":     "2026-06-15T12:00:00+00:00",
                          "status":   {"short": "NS"}},
             "league":  {"id": 39, "name": "Premier League"},
             "teams":   {"home": {"name": f"H{i}"}, "away": {"name": f"A{i}"}},
             "timestamp": int(datetime.now(timezone.utc).timestamp()) + 3600 + i}
            for i in range(8)
        ]
        af_spy = AsyncMock(return_value=[])
        with patch("services.external_sources.thestatsapi_fixtures_adapter.fetch_fixtures_next_48h",
                    AsyncMock(return_value=(ts_fixtures, ["THESTATSAPI_FIXTURES_SUCCESS"]))), \
             patch("services.data_ingestion.af.fixtures_next_48h", af_spy):
            client = object()
            fixtures, audit = await di._discover_football_fixtures(client)

        assert len(fixtures) == 8
        assert audit["primary_winner"] == "thestatsapi"
        assert audit["counts_per_src"]["thestatsapi"] == 8
        assert audit["merged"] is False
        # API-Football MUST NOT have been called when TS wins early.
        af_spy.assert_not_awaited()
        # Every fixture must carry the discovery source stamp.
        for f in fixtures:
            assert f["_discovery_source"] == "thestatsapi"

    @pytest.mark.asyncio
    async def test_thestatsapi_empty_falls_through_to_apifootball(self, monkeypatch):
        monkeypatch.delenv("ENABLE_THESTATSAPI_FIXTURES_PRIMARY", raising=False)
        af_fixtures = [_make_af_fixture(i) for i in range(10)]
        with patch("services.external_sources.thestatsapi_fixtures_adapter.fetch_fixtures_next_48h",
                    AsyncMock(return_value=([], ["THESTATSAPI_FIXTURES_EMPTY"]))), \
             patch("services.data_ingestion.af.fixtures_next_48h",
                    AsyncMock(return_value=af_fixtures)):
            fixtures, audit = await di._discover_football_fixtures(object())

        assert audit["primary_winner"] == "api_football"
        assert audit["counts_per_src"]["api_football"] == 10
        assert len(fixtures) == 10
        assert all(f["_discovery_source"] == "api_football" for f in fixtures)

    @pytest.mark.asyncio
    async def test_thestatsapi_disabled_skips_to_apifootball(self, monkeypatch):
        monkeypatch.setenv("ENABLE_THESTATSAPI_FIXTURES_PRIMARY", "false")
        af_fixtures = [_make_af_fixture(i) for i in range(6)]
        ts_spy = AsyncMock()
        with patch("services.external_sources.thestatsapi_fixtures_adapter.fetch_fixtures_next_48h", ts_spy), \
             patch("services.data_ingestion.af.fixtures_next_48h",
                    AsyncMock(return_value=af_fixtures)):
            fixtures, audit = await di._discover_football_fixtures(object())

        ts_spy.assert_not_awaited()
        assert "thestatsapi" not in audit["sources_called"]
        assert audit["primary_winner"] == "api_football"


# =====================================================================
# F87.a — TheStatsAPI adapter normalisation
# =====================================================================
class TestTheStatsAPIAdapterNormalise:
    def test_normalise_basic_fixture(self):
        raw = _make_ts_fixture(1, league="UEFA Champions League")
        fx = ts_fx._normalise_fixture(raw)
        assert fx is not None
        assert fx["_external_source"] == "thestatsapi"
        assert fx["league"]["name"] == "UEFA Champions League"
        assert fx["status"]["short"] == "NS"  # mapped from "scheduled"
        assert fx["fixture"]["timestamp"] == raw["timestamp"]

    def test_normalise_international_flags(self):
        raw = _make_ts_fixture(1, league="World Cup Qualification Europe")
        raw["league"]["country"] = "World"
        fx = ts_fx._normalise_fixture(raw)
        assert fx["_is_international"] is True
        assert fx["_is_national_team"] is True

    def test_normalise_unparseable_returns_none(self):
        for bad in [{}, {"id": None}, "not a dict", 42]:
            assert ts_fx._normalise_fixture(bad) is None

    @pytest.mark.asyncio
    async def test_adapter_disabled_returns_empty(self, monkeypatch):
        monkeypatch.setenv("ENABLE_THESTATSAPI_FIXTURES_PRIMARY", "false")
        fx, codes = await ts_fx.fetch_fixtures_next_48h(object())
        assert fx == []
        assert ts_fx.RC_DISABLED in codes


# =====================================================================
# F87.b — Sofascore adapters
# =====================================================================
class TestSofascoreAdapter:
    def test_normalise_playwright_shape(self):
        ev = {
            "id":         "sofa-123",
            "league":     "Premier League - England",
            "kickoff_iso": "2026-06-15T15:00:00+00:00",
            "is_live":    False,
            "home_team":  {"name": "Liverpool"},
            "away_team":  {"name": "Man City"},
        }
        fx = so_fx._normalise_sofascore_event(ev, source_tag="sofascore_pw")
        assert fx is not None
        assert fx["_external_source"] == "sofascore_pw"
        assert fx["_external_source_id"] == "123"
        assert fx["teams"]["home"]["name"] == "Liverpool"
        assert fx["league"]["name"] == "Premier League"
        assert fx["status"]["short"] == "NS"

    def test_normalise_raw_sofascore_json(self):
        ev = {
            "id":             999,
            "startTimestamp": int(datetime.now(timezone.utc).timestamp()) + 7200,
            "tournament":     {"name": "Liga MX",
                                "category": {"name": "Mexico", "slug": "mexico"}},
            "status":         {"type": "notstarted"},
            "homeTeam":       {"name": "Club América"},
            "awayTeam":       {"name": "Chivas"},
        }
        fx = so_fx._normalise_sofascore_event(ev, source_tag="scrapedo")
        assert fx is not None
        assert fx["_external_source"] == "scrapedo"
        assert fx["league"]["country"] == "Mexico"
        assert fx["status"]["short"] == "NS"

    def test_normalise_in_progress(self):
        ev = {"id": 5, "startTimestamp": 1700000000,
              "tournament": {"name": "Bundesliga"},
              "status": {"type": "inprogress"},
              "homeTeam": {"name": "H"}, "awayTeam": {"name": "A"}}
        fx = so_fx._normalise_sofascore_event(ev)
        assert fx["status"]["short"] == "1H"

    @pytest.mark.asyncio
    async def test_adapter_disabled(self, monkeypatch):
        monkeypatch.setenv("ENABLE_SOFASCORE_PW_FALLBACK", "false")
        assert await so_fx.fetch_fixtures_today() == []

    @pytest.mark.asyncio
    async def test_scrapedo_adapter_disabled(self, monkeypatch):
        monkeypatch.setenv("ENABLE_SCRAPEDO_FIXTURES_FALLBACK", "false")
        assert await sd_fx.fetch_fixtures_today() == []

    @pytest.mark.asyncio
    async def test_scrapedo_no_token_returns_empty(self, monkeypatch):
        monkeypatch.delenv("SCRAPEDO_TOKEN", raising=False)
        monkeypatch.delenv("SCRAPEDO_API_KEY", raising=False)
        # Function must NOT raise even with the token missing.
        out = await sd_fx.fetch_fixtures_today()
        assert out == []


# =====================================================================
# F87.c — Unknown competition bucket
# =====================================================================
class TestF87cUnknownBucket:
    def test_unknown_competition_passes_with_low_priority(self, monkeypatch):
        monkeypatch.delenv("ENABLE_UNKNOWN_COMPETITION_BUCKET", raising=False)
        meta = fc.get_unknown_competition_meta("Some Random Cup 2026")
        assert meta is not None
        assert meta["tier"] == "unknown"
        assert meta["priority"] == fc.UNKNOWN_TIER_PRIORITY
        assert meta["_unknown_bucket"] is True

    @pytest.mark.parametrize("name", [
        "Premier League U16",
        "Bayern Munich Reserves",
        "Friendly Clubs",
        "Club Friendlies",
        "Youth League Spain",
        "Regional League Bavaria",
        "Division 4",
        "Tercera División RFEF",
        "5th Division",
    ])
    def test_blocklisted_returns_none(self, name):
        # Either get_unknown_competition_meta returns None for them …
        meta = fc.get_unknown_competition_meta(name)
        # … and the blocklist detector flags them.
        assert fc.is_competition_blocklisted(name) is True
        assert meta is None, f"{name!r} should be blocklisted"

    @pytest.mark.parametrize("name", [
        "FIFA Club World Cup",
        "CONMEBOL Libertadores",
        "UEFA Conference League",
        "International Friendlies",  # NOT "friendly clubs"
        "Asian Champions League",
    ])
    def test_inclusive_competitions_pass(self, name):
        assert fc.is_competition_blocklisted(name) is False
        meta = fc.get_unknown_competition_meta(name)
        assert meta is not None
        assert meta["canonical_name"] == name

    def test_flag_off_disables_bucket(self, monkeypatch):
        monkeypatch.setenv("ENABLE_UNKNOWN_COMPETITION_BUCKET", "false")
        assert fc.get_unknown_competition_meta("Random Cup") is None

    def test_get_allowed_tiers_includes_unknown_when_flag_on(self, monkeypatch):
        monkeypatch.delenv("ENABLE_UNKNOWN_COMPETITION_BUCKET", raising=False)
        tiers = fc.get_allowed_tiers()
        assert "unknown" in tiers
        # When the flag is off, unknown disappears.
        monkeypatch.setenv("ENABLE_UNKNOWN_COMPETITION_BUCKET", "false")
        tiers_off = fc.get_allowed_tiers()
        assert "unknown" not in tiers_off


# =====================================================================
# Cascade merge — multi-source dedupe
# =====================================================================
class TestF87Merge:
    @pytest.mark.asyncio
    async def test_merge_dedupes_across_sources(self, monkeypatch):
        # All four sources return < MIN_VIABLE_COUNT — cascade must merge.
        monkeypatch.delenv("ENABLE_THESTATSAPI_FIXTURES_PRIMARY", raising=False)
        same_kickoff = int(datetime.now(timezone.utc).timestamp()) + 3600
        ts_fixtures = [{
            "id": "ts-1", "_external_source": "thestatsapi",
            "fixture": {"id": "ts-1", "timestamp": same_kickoff,
                         "date": "2026-06-15T15:00:00+00:00",
                         "status": {"short": "NS"}},
            "league":  {"id": 39, "name": "Premier League"},
            "teams":   {"home": {"name": "Liverpool FC"},
                         "away": {"name": "Manchester City"}},
            "timestamp": same_kickoff,
        }]
        # Same fixture re-discovered by Sofascore (different prefix, dedupe key).
        sofa_fixtures = [{
            "id": "sofa-9", "_external_source": "sofascore_pw",
            "fixture": {"id": "sofa-9", "timestamp": same_kickoff,
                         "date": "2026-06-15T15:00:00+00:00",
                         "status": {"short": "NS"}},
            "league":  {"id": None, "name": "Premier League"},
            "teams":   {"home": {"name": "Liverpool"},          # FC suffix dropped
                         "away": {"name": "Manchester City FC"}}, # FC suffix dropped → matches "Manchester City"
            "timestamp": same_kickoff,
        }]
        with patch("services.external_sources.thestatsapi_fixtures_adapter.fetch_fixtures_next_48h",
                    AsyncMock(return_value=(ts_fixtures, ["THESTATSAPI_FIXTURES_SUCCESS"]))), \
             patch("services.data_ingestion.af.fixtures_next_48h",
                    AsyncMock(return_value=[])), \
             patch("services.data_ingestion.fb.espn_soccer_scoreboard",
                    AsyncMock(return_value=[])), \
             patch("services.external_sources.sofascore_fixtures_adapter.fetch_fixtures_today",
                    AsyncMock(return_value=sofa_fixtures)), \
             patch("services.external_sources.scrapedo_fixtures_adapter.fetch_fixtures_today",
                    AsyncMock(return_value=[])):
            fixtures, audit = await di._discover_football_fixtures(object())

        assert audit["merged"] is True
        assert audit["primary_winner"] is None
        # The TheStatsAPI entry wins (highest priority bucket).
        assert len(fixtures) == 1
        assert fixtures[0]["_discovery_source"] == "thestatsapi"

    def test_dedupe_key_normalises_suffixes(self):
        ts = 1700000000
        fx_a = {"timestamp": ts,
                "teams": {"home": {"name": "Liverpool FC"},
                           "away": {"name": "Manchester City"}}}
        fx_b = {"timestamp": ts,
                "teams": {"home": {"name": "Liverpool"},
                           "away": {"name": "Man City"}}}
        # FC suffix gets dropped; "Man" prefix is NOT canonicalised so
        # the dedupe key may differ. That's OK — the dedupe is best-effort.
        assert di._fixture_dedupe_key(fx_a)[0] == "liverpool"
        assert di._fixture_dedupe_key(fx_b)[0] == "liverpool"

    def test_merge_priority_order_respected(self):
        # When buckets disagree, the first one in priority order wins.
        ts = 1700000000
        common = {
            "timestamp": ts,
            "teams":     {"home": {"name": "A"}, "away": {"name": "B"}},
            "fixture":   {"id": "x", "timestamp": ts},
        }
        buckets = {
            "thestatsapi":  [{**common, "id": "ts-1", "_external_source": "thestatsapi"}],
            "api_football": [{**common, "id": "af-1", "_external_source": "api_football"}],
        }
        merged = di._merge_fixture_buckets(buckets)
        assert len(merged) == 1
        assert merged[0]["_discovery_source"] == "thestatsapi"


# =====================================================================
# Cascade is fail-soft (broken source doesn't kill the cascade)
# =====================================================================
class TestFailSoftCascade:
    @pytest.mark.asyncio
    async def test_broken_thestatsapi_still_falls_back(self, monkeypatch):
        monkeypatch.delenv("ENABLE_THESTATSAPI_FIXTURES_PRIMARY", raising=False)
        af_fixtures = [_make_af_fixture(i) for i in range(7)]
        with patch("services.external_sources.thestatsapi_fixtures_adapter.fetch_fixtures_next_48h",
                    AsyncMock(side_effect=RuntimeError("boom"))), \
             patch("services.data_ingestion.af.fixtures_next_48h",
                    AsyncMock(return_value=af_fixtures)):
            fixtures, audit = await di._discover_football_fixtures(object())

        assert "thestatsapi" in audit["reason_codes"]
        assert audit["reason_codes"]["thestatsapi"] == ["EXCEPTION"]
        assert audit["primary_winner"] == "api_football"
        assert len(fixtures) == 7
