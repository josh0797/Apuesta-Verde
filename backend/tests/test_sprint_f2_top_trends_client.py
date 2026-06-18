"""Sprint F.2 — tests for the 365Scores Top Trends client.

All HTTP transport is mocked. The fixture at
``tests/fixtures/365scores_top_trends_4627854.json`` is the verbatim
production response captured from
``https://webws.365scores.com/web/trends/?games=4627854`` on the
discovery day.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

import pytest

from services.external_sources import (
    three65scores_top_trends_client as ttc,
)
from services.external_sources import (
    three65scores_identity_resolver as id_resolver,
)


FIXTURES_DIR = Path(__file__).parent / "fixtures"
FIX_TRENDS_PATH = FIXTURES_DIR / "365scores_top_trends_4627854.json"

# Test-only constants (fixture-scoped).
FIX_GAME_ID         = 4627854
FIX_HOME_TEAM_ID    = 5106   # Mexico
FIX_AWAY_TEAM_ID    = 2383   # South Korea
FIX_INTERNAL_ID     = "test:365scores:game:4627854"
FIX_KICKOFF         = datetime(2026, 6, 17, 22, 0, 0, tzinfo=timezone.utc)


def _load_fixture() -> dict:
    return json.loads(FIX_TRENDS_PATH.read_text())


# ════════════════════════════════════════════════════════════════════════
# Helpers — Fake Mongo (reused pattern)
# ════════════════════════════════════════════════════════════════════════
class FakeCollection:
    def __init__(self):
        self.docs: list[dict] = []
        self.indexes_created: list[dict] = []

    async def create_index(self, key, **kwargs):
        self.indexes_created.append({"key": key, **kwargs})
        return kwargs.get("name") or "ix"

    async def find_one(self, query):
        for d in self.docs:
            if all(d.get(k) == v for k, v in (query or {}).items()):
                return d
        return None

    async def update_one(self, query, update, upsert=False):
        for i, d in enumerate(self.docs):
            if all(d.get(k) == v for k, v in (query or {}).items()):
                self.docs[i] = {**d, **(update.get("$set") or {})}
                return {"matched": 1}
        if upsert:
            new = dict(query or {})
            new.update(update.get("$set") or {})
            self.docs.append(new)
        return {"matched": 0, "upserted": int(upsert)}

    async def insert_one(self, doc):
        self.docs.append(dict(doc))


class FakeDB:
    def __init__(self):
        self.football_365scores_top_trends = FakeCollection()
        self.football_365scores_identities = FakeCollection()


# ════════════════════════════════════════════════════════════════════════
# Fixture availability
# ════════════════════════════════════════════════════════════════════════
class TestFixture:
    def test_fixture_file_exists(self):
        assert FIX_TRENDS_PATH.exists(), (
            f"fixture missing: {FIX_TRENDS_PATH} — run "
            "scripts/run_sprint_f2_capture_fixture.py"
        )

    def test_fixture_has_expected_top_level_keys(self):
        d = _load_fixture()
        assert isinstance(d.get("trends"), list)
        assert len(d["trends"]) > 0
        assert all(isinstance(t, dict) for t in d["trends"])


# ════════════════════════════════════════════════════════════════════════
# Pure parser
# ════════════════════════════════════════════════════════════════════════
class TestNormalizeTrend:
    def test_normalises_money_line_with_sample(self):
        raw = {
            "id": 11584513, "lineTypeId": 1,
            "text": "Mexico won - 4/4 Last Matches",
            "cause": "Mexico won", "betCTA": "Mexico to win",
            "isTop": True,
            "competitorIds": [FIX_HOME_TEAM_ID],
            "gameId": FIX_GAME_ID,
            "odds": {"rate": {"decimal": 2.0}, "bookmakerId": 103,
                      "oldRate": {"decimal": 1.95},
                      "originalRate": {"decimal": 1.75}, "trend": 3},
            "percentage": 1.0,
        }
        row = ttc.normalize_trend(
            raw, home_team_id=FIX_HOME_TEAM_ID, away_team_id=FIX_AWAY_TEAM_ID,
            home_team_name="Mexico", away_team_name="South Korea",
        )
        assert row is not None
        assert row["trend_id"] == 11584513
        assert row["is_top"] is True
        assert row["line_type_id"] == 1
        assert row["market"] == "ML"
        assert row["sample"] == {"hits": 4, "total": 4, "rate": 1.0}
        assert row["period"] == "last_4_matches"
        assert row["scope"] == "all"
        assert row["team_side"] == "home"
        assert row["team_name"] == "Mexico"
        assert row["confidence"] == ttc.CONFIDENCE_MEDIUM  # 4/4 below total>=5 threshold but isTop floor
        assert row["odds"]["decimal"] == 2.0
        assert row["odds"]["decimal_old"] == 1.95
        assert row["odds"]["decimal_original"] == 1.75

    def test_detects_away_scope_from_text(self):
        raw = {
            "id": 11766272, "lineTypeId": 3,
            "text": "Under 2.5 Goals Away - 7/8 Last Matches",
            "competitorIds": [FIX_AWAY_TEAM_ID],
            "isTop": False, "percentage": 0.875,
        }
        row = ttc.normalize_trend(
            raw, home_team_id=FIX_HOME_TEAM_ID, away_team_id=FIX_AWAY_TEAM_ID,
            home_team_name="Mexico", away_team_name="South Korea",
        )
        assert row["scope"] == "away"
        assert row["team_side"] == "away"
        assert row["team_name"] == "South Korea"
        assert row["sample"] == {"hits": 7, "total": 8, "rate": 0.875}
        # total=8 >= 5 and rate=0.875 >= 0.70 → MEDIUM
        assert row["confidence"] == ttc.CONFIDENCE_MEDIUM
        assert row["market"] == "OU_GOALS"

    def test_first_half_scope_from_line_type(self):
        raw = {
            "id": 99, "lineTypeId": 5,
            "text": "Mexico won the first half - 6/7 Last Matches",
            "competitorIds": [FIX_HOME_TEAM_ID],
            "percentage": 0.857,
        }
        row = ttc.normalize_trend(
            raw, home_team_id=FIX_HOME_TEAM_ID, away_team_id=FIX_AWAY_TEAM_ID,
        )
        assert row["scope"] == "first_half"
        assert row["market"] == "1H_ML"

    def test_btts_line_type_12_mapped(self):
        raw = {
            "id": 1, "lineTypeId": 12, "text": "Both teams scored - 3/4 Last Matches",
            "competitorIds": [FIX_HOME_TEAM_ID, FIX_AWAY_TEAM_ID],
            "percentage": 0.75,
        }
        row = ttc.normalize_trend(
            raw, home_team_id=FIX_HOME_TEAM_ID, away_team_id=FIX_AWAY_TEAM_ID,
        )
        assert row["market"] == "BTTS"
        assert row["team_side"] == "both"
        assert row["team_name"] is None

    def test_unknown_line_type_kept_verbatim(self):
        raw = {
            "id": 1, "lineTypeId": 999, "text": "Custom trend - 2/3 last matches",
            "competitorIds": [],
        }
        row = ttc.normalize_trend(raw)
        assert row["market"] == "LINE_TYPE_999"

    def test_returns_none_for_garbage_input(self):
        assert ttc.normalize_trend(None) is None
        assert ttc.normalize_trend({}) is None
        assert ttc.normalize_trend("foo") is None

    def test_high_confidence_when_sample_total_ge_10(self):
        raw = {
            "id": 1, "lineTypeId": 1,
            "text": "Team won - 10/12 Last Matches",
            "competitorIds": [FIX_HOME_TEAM_ID],
            "percentage": 0.833,
        }
        row = ttc.normalize_trend(raw, home_team_id=FIX_HOME_TEAM_ID)
        # total=12 >= 10 AND pct=0.83 >= 0.80 → HIGH
        assert row["confidence"] == ttc.CONFIDENCE_HIGH


# ════════════════════════════════════════════════════════════════════════
# Payload normaliser using REAL fixture
# ════════════════════════════════════════════════════════════════════════
class TestNormalizeFixture:
    def test_normalises_all_12_trends_from_fixture(self):
        payload = _load_fixture()
        rows = ttc.normalize_trends_payload(
            payload,
            home_team_id=FIX_HOME_TEAM_ID, away_team_id=FIX_AWAY_TEAM_ID,
            home_team_name="Mexico", away_team_name="South Korea",
        )
        assert len(rows) == 12
        # Every row has the canonical keys.
        for r in rows:
            assert "raw" in r and "market" in r and "team_side" in r
            assert "confidence" in r and "sample" in r
            assert r["language"] == "en"

    def test_fixture_top_trends_flag(self):
        payload = _load_fixture()
        rows = ttc.normalize_trends_payload(
            payload,
            home_team_id=FIX_HOME_TEAM_ID, away_team_id=FIX_AWAY_TEAM_ID,
        )
        top_rows = [r for r in rows if r["is_top"]]
        # The fixture has at least 2 isTop=True rows.
        assert len(top_rows) >= 2

    def test_fixture_correctly_classifies_home_away_both(self):
        payload = _load_fixture()
        rows = ttc.normalize_trends_payload(
            payload,
            home_team_id=FIX_HOME_TEAM_ID, away_team_id=FIX_AWAY_TEAM_ID,
            home_team_name="Mexico", away_team_name="South Korea",
        )
        sides = {r["team_side"] for r in rows}
        assert "home" in sides
        assert "away" in sides
        assert "both" in sides


# ════════════════════════════════════════════════════════════════════════
# Low-level fetch_top_trends — transport injection
# ════════════════════════════════════════════════════════════════════════
@pytest.mark.asyncio
class TestFetchTopTrends:
    async def test_happy_path_returns_normalised_trends(self):
        payload = _load_fixture()
        captured_urls: list[str] = []

        async def fake_transport(url: str):
            captured_urls.append(url)
            return {"ok": True, "payload": payload}

        out = await ttc.fetch_top_trends(
            game_id=FIX_GAME_ID,
            home_team_id=FIX_HOME_TEAM_ID, away_team_id=FIX_AWAY_TEAM_ID,
            home_team_name="Mexico", away_team_name="South Korea",
            transport=fake_transport,
        )
        assert out["available"] is True
        assert out["reason_code"] == ttc.RC_TRENDS_FOUND
        assert out["trends_count"] == 12
        assert out["top_trends_count"] >= 2
        assert out["from_cache"] is False
        assert len(captured_urls) == 1
        # Ensure the URL respects the discovered contract.
        u = captured_urls[0]
        assert "/web/trends/" in u
        assert f"games={FIX_GAME_ID}" in u
        assert "appTypeId=5" in u
        assert "langId=1" in u  # en default

    async def test_only_top_filter(self):
        payload = _load_fixture()

        async def fake_transport(_url):
            return {"ok": True, "payload": payload}

        out = await ttc.fetch_top_trends(
            game_id=FIX_GAME_ID, transport=fake_transport, only_top=True,
        )
        # Only the isTop rows are exposed.
        assert all(t["is_top"] for t in out["trends"])
        assert out["trends_count"] == out["top_trends_count"]

    async def test_language_es_uses_langId_29(self):
        async def fake_transport(url: str):
            assert "langId=29" in url
            return {"ok": True, "payload": _load_fixture()}

        out = await ttc.fetch_top_trends(
            game_id=FIX_GAME_ID, transport=fake_transport, language="es",
        )
        assert out["language"] == "es"

    async def test_missing_game_id_returns_explicit_error(self):
        out = await ttc.fetch_top_trends(game_id=0)
        assert out["available"] is False
        assert out["reason_code"] == ttc.RC_GAME_ID_MISSING

    async def test_transport_failure_propagated(self):
        async def fake_transport(_url):
            return {"ok": False,
                    "reason_code": "SCORE365_BLOCKED_OR_FORBIDDEN",
                    "status_code": 403,
                    "message_debug": "Cloudflare challenge"}

        out = await ttc.fetch_top_trends(
            game_id=FIX_GAME_ID, transport=fake_transport,
        )
        assert out["available"] is False
        assert out["reason_code"] == "SCORE365_BLOCKED_OR_FORBIDDEN"
        assert out["status_code"] == 403

    async def test_transport_exception_caught(self):
        async def fake_transport(_url):
            raise TimeoutError("scrape.do upstream timeout")

        out = await ttc.fetch_top_trends(
            game_id=FIX_GAME_ID, transport=fake_transport,
        )
        assert out["available"] is False
        assert out["reason_code"] == ttc.RC_TRANSPORT_ERROR
        assert "timeout" in (out.get("message_debug") or "").lower()

    async def test_empty_trends_returns_explicit_empty(self):
        async def fake_transport(_url):
            return {"ok": True, "payload": {"trends": []}}

        out = await ttc.fetch_top_trends(
            game_id=FIX_GAME_ID, transport=fake_transport,
        )
        assert out["available"] is False
        assert out["reason_code"] == ttc.RC_TRENDS_EMPTY


# ════════════════════════════════════════════════════════════════════════
# Cache behaviour
# ════════════════════════════════════════════════════════════════════════
@pytest.mark.asyncio
class TestCache:
    async def test_persists_to_mongo_after_fresh_fetch(self):
        db = FakeDB()
        payload = _load_fixture()

        async def fake_transport(_url):
            return {"ok": True, "payload": payload}

        out = await ttc.fetch_top_trends(
            game_id=FIX_GAME_ID, transport=fake_transport, db=db,
        )
        assert out["available"] is True
        coll = db.football_365scores_top_trends
        assert len(coll.docs) == 1
        stored = coll.docs[0]
        assert stored["game_id"] == FIX_GAME_ID
        assert stored["language"] == "en"
        assert isinstance(stored["fetched_at"], datetime)
        assert stored["trends_count"] == 12

    async def test_cache_hit_skips_transport(self):
        db = FakeDB()
        # Pre-populate cache with a "fresh" record.
        await db.football_365scores_top_trends.insert_one({
            "game_id": FIX_GAME_ID, "language": "en",
            "trends": [{"raw": "cached", "is_top": True, "market": "ML"}],
            "trends_count": 1, "top_trends_count": 1,
            "fetched_at": datetime.now(timezone.utc) - timedelta(minutes=5),
            "source": ttc.SOURCE_LABEL,
        })
        called: list[str] = []

        async def fake_transport(url):
            called.append(url)
            return {"ok": True, "payload": _load_fixture()}

        out = await ttc.fetch_top_trends(
            game_id=FIX_GAME_ID, transport=fake_transport, db=db,
        )
        assert out["available"] is True
        assert out["reason_code"] == ttc.RC_FROM_CACHE
        assert out["from_cache"] is True
        assert out["trends_count"] == 1
        assert called == []  # network skipped

    async def test_force_refresh_bypasses_cache(self):
        db = FakeDB()
        await db.football_365scores_top_trends.insert_one({
            "game_id": FIX_GAME_ID, "language": "en",
            "trends": [], "trends_count": 0, "top_trends_count": 0,
            "fetched_at": datetime.now(timezone.utc) - timedelta(minutes=5),
            "source": ttc.SOURCE_LABEL,
        })
        called: list[str] = []

        async def fake_transport(url):
            called.append(url)
            return {"ok": True, "payload": _load_fixture()}

        out = await ttc.fetch_top_trends(
            game_id=FIX_GAME_ID, transport=fake_transport, db=db,
            force_refresh=True,
        )
        assert out["available"] is True
        assert out["from_cache"] is False
        assert len(called) == 1
        # And the cache has been refreshed.
        stored = next(d for d in db.football_365scores_top_trends.docs
                      if d["game_id"] == FIX_GAME_ID)
        assert stored["trends_count"] == 12

    async def test_stale_cache_is_ignored(self):
        db = FakeDB()
        # Cache older than TTL.
        await db.football_365scores_top_trends.insert_one({
            "game_id": FIX_GAME_ID, "language": "en",
            "trends": [{"raw": "old"}], "trends_count": 1,
            "top_trends_count": 0,
            "fetched_at": datetime.now(timezone.utc) - timedelta(hours=2),
            "source": ttc.SOURCE_LABEL,
        })
        called: list[str] = []

        async def fake_transport(_url):
            called.append("hit")
            return {"ok": True, "payload": _load_fixture()}

        out = await ttc.fetch_top_trends(
            game_id=FIX_GAME_ID, transport=fake_transport, db=db,
            cache_ttl_seconds=30 * 60,  # 30 min TTL → 2h doc is stale
        )
        assert out["available"] is True
        assert out["from_cache"] is False
        assert len(called) == 1

    async def test_ensure_indexes_creates_ttl_and_unique(self):
        db = FakeDB()
        report = await ttc.ensure_indexes(db)
        assert "ix_game_language" in report["created"]
        assert "ttl_fetched_at" in report["created"]
        idx = db.football_365scores_top_trends.indexes_created
        names = [i.get("name") for i in idx]
        assert "ix_game_language" in names
        assert "ttl_fetched_at" in names


# ════════════════════════════════════════════════════════════════════════
# High-level entry — identity + trends in one call
# ════════════════════════════════════════════════════════════════════════
@pytest.mark.asyncio
class TestFetchForMatch:
    async def test_resolves_identity_then_fetches_trends(self):
        db = FakeDB()

        async def detail_fetcher(_game_id: str):
            return {
                "game": {
                    "id":             FIX_GAME_ID,
                    "competitionId":  5930,
                    "startTime":      FIX_KICKOFF.isoformat(),
                    "competitors":    [
                        {"id": FIX_HOME_TEAM_ID, "name": "Mexico",
                         "isHome": True},
                        {"id": FIX_AWAY_TEAM_ID, "name": "South Korea",
                         "isHome": False},
                    ],
                },
            }

        async def trends_transport(_url):
            return {"ok": True, "payload": _load_fixture()}

        out = await ttc.fetch_top_trends_for_match(
            internal_match_id=FIX_INTERNAL_ID,
            home_team="Mexico", away_team="South Korea",
            commence_time=FIX_KICKOFF,
            competition_id=5930,
            match_url=(
                "https://www.365scores.com/football/match/"
                f"fifa-world-cup-5930/mexico-south-korea-"
                f"{FIX_AWAY_TEAM_ID}-{FIX_HOME_TEAM_ID}-{FIX_GAME_ID}"
            ),
            game_detail_fetcher=detail_fetcher,
            transport=trends_transport,
            db=db,
        )
        assert out["available"] is True
        assert out["trends_count"] == 12
        assert out["identity"]["game_id"] == FIX_GAME_ID
        assert out["identity"]["status"] == id_resolver.STATUS_RESOLVED

    async def test_identity_failure_short_circuits(self):
        async def trends_transport(_url):
            raise AssertionError("trends transport should not be hit "
                                 "when identity fails")

        out = await ttc.fetch_top_trends_for_match(
            internal_match_id=FIX_INTERNAL_ID,
            home_team="Mexico", away_team="South Korea",
            commence_time=FIX_KICKOFF,
            # No fetchers + no URL → identity → SOURCE_UNAVAILABLE.
            transport=trends_transport,
        )
        assert out["available"] is False
        assert out["reason_code"] == ttc.RC_IDENTITY_NOT_RESOLVED
        assert out["identity_status"] == id_resolver.STATUS_SOURCE_UNAVAILABLE
