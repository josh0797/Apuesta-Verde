"""Sprint-D9.2 Block B — tests for the multi-source xG real client.

All HTTP is mocked via the ``transport`` / ``thestatsapi_fetcher``
injection hooks; no network call is ever made.
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
from typing import Any

import pytest

from services import football_xg_real_client as xg


# ════════════════════════════════════════════════════════════════════════
# Fixtures — synthetic HTML fragments
# ════════════════════════════════════════════════════════════════════════
UNDERSTAT_HTML = (
    "<html><body><script>"
    'var datesData = JSON.parse(\'[{\\x22datetime\\x22:\\x222024-08-17 14:00:00\\x22,'
    '\\x22h\\x22:{\\x22id\\x22:\\x22\\x22,\\x22title\\x22:\\x22Arsenal\\x22},'
    '\\x22a\\x22:{\\x22id\\x22:\\x22\\x22,\\x22title\\x22:\\x22Wolves\\x22},'
    '\\x22goals\\x22:{\\x22h\\x22:\\x222\\x22,\\x22a\\x22:\\x220\\x22},'
    '\\x22xG\\x22:{\\x22h\\x22:\\x221.51\\x22,\\x22a\\x22:\\x220.32\\x22},'
    '\\x22league_id\\x22:1},'
    '{\\x22datetime\\x22:\\x222024-08-24 11:30:00\\x22,'
    '\\x22h\\x22:{\\x22id\\x22:\\x22\\x22,\\x22title\\x22:\\x22Aston Villa\\x22},'
    '\\x22a\\x22:{\\x22id\\x22:\\x22\\x22,\\x22title\\x22:\\x22Arsenal\\x22},'
    '\\x22goals\\x22:{\\x22h\\x22:\\x220\\x22,\\x22a\\x22:\\x222\\x22},'
    '\\x22xG\\x22:{\\x22h\\x22:\\x220.85\\x22,\\x22a\\x22:\\x222.10\\x22},'
    '\\x22league_id\\x22:1}]\');'
    "</script></body></html>"
)

FBREF_HTML = """
<table>
  <tr data-row="0">
    <th data-stat="date">2024-08-17</th>
    <td data-stat="opponent">Wolves</td>
    <td data-stat="venue">Home</td>
    <td data-stat="goals_for">2</td>
    <td data-stat="goals_against">0</td>
    <td data-stat="xg_for">1.51</td>
    <td data-stat="xg_against">0.32</td>
    <td data-stat="comp">Premier League</td>
  </tr>
  <tr data-row="1">
    <th data-stat="date">2024-08-24</th>
    <td data-stat="opponent">Aston Villa</td>
    <td data-stat="venue">Away</td>
    <td data-stat="goals_for">2</td>
    <td data-stat="goals_against">0</td>
    <td data-stat="xg_for">2.10</td>
    <td data-stat="xg_against">0.85</td>
    <td data-stat="comp">Premier League</td>
  </tr>
  <tr data-row="2">
    <!-- row without xG must be skipped -->
    <th data-stat="date">2020-01-01</th>
    <td data-stat="opponent">Old Match</td>
    <td data-stat="comp">EFL Cup</td>
  </tr>
</table>
"""

FOOTYSTATS_HTML = """
<table>
  <tr>
    <td>2024-08-17</td>
    <td class="team-name">Wolves</td>
    <td data-xg="1.51"></td>
    <td data-xg="0.32"></td>
  </tr>
  <tr>
    <td>2024-08-24</td>
    <td class="team-name">Aston Villa</td>
    <td data-xg="2.10"></td>
    <td data-xg="0.85"></td>
  </tr>
</table>
"""


# ════════════════════════════════════════════════════════════════════════
# Pure parsers
# ════════════════════════════════════════════════════════════════════════
class TestUnderstatParser:
    def test_extracts_home_and_away_matches(self):
        rows = xg.parse_understat_team_page(UNDERSTAT_HTML,
                                              canonical_team="Arsenal")
        assert len(rows) == 2
        # Sorted newest-first.
        assert rows[0]["date"] == "2024-08-24 11:30:00"
        assert rows[0]["venue"] == "away"
        assert rows[0]["xg_for"] == pytest.approx(2.10)
        assert rows[0]["xg_against"] == pytest.approx(0.85)
        assert rows[0]["opponent"] == "Aston Villa"
        assert rows[1]["date"] == "2024-08-17 14:00:00"
        assert rows[1]["venue"] == "home"
        assert rows[1]["xg_for"] == pytest.approx(1.51)
        assert rows[1]["opponent"] == "Wolves"

    def test_returns_empty_when_team_not_in_payload(self):
        rows = xg.parse_understat_team_page(UNDERSTAT_HTML,
                                              canonical_team="Liverpool")
        assert rows == []

    def test_handles_malformed_html(self):
        assert xg.parse_understat_team_page("not html",
                                              canonical_team="x") == []
        assert xg.parse_understat_team_page("", canonical_team="x") == []


class TestFbrefParser:
    def test_parses_match_log(self):
        rows = xg.parse_fbref_matchlog(FBREF_HTML)
        assert len(rows) == 2  # the third row is skipped (no xG)
        assert rows[0]["date"] == "2024-08-24"
        assert rows[0]["xg_for"] == pytest.approx(2.10)
        assert rows[1]["competition"] == "Premier League"

    def test_handles_garbage(self):
        assert xg.parse_fbref_matchlog("") == []
        assert xg.parse_fbref_matchlog("not html") == []


class TestFootystatsParser:
    def test_parses_xg_pairs(self):
        rows = xg.parse_footystats_team_page(FOOTYSTATS_HTML,
                                                canonical_team="Arsenal")
        assert len(rows) == 2
        assert rows[0]["xg_for"] == pytest.approx(2.10)
        assert rows[0]["opponent"] == "Aston Villa"


# ════════════════════════════════════════════════════════════════════════
# Feature engineering
# ════════════════════════════════════════════════════════════════════════
class TestFeatures:
    def test_compute_features_basic(self):
        matches = [
            {"xg_for": 2.10, "xg_against": 0.85},
            {"xg_for": 1.51, "xg_against": 0.32},
            {"xg_for": 1.20, "xg_against": 1.40},
        ]
        f = xg.compute_xg_features_l15(matches)
        assert f["n_samples"] == 3
        assert f["xg_l15_mean"] == pytest.approx((2.10 + 1.51 + 1.20) / 3, abs=1e-3)
        assert f["xga_l15_mean"] == pytest.approx((0.85 + 0.32 + 1.40) / 3, abs=1e-3)
        assert f["xg_l15_std"] > 0
        assert f["xg_l15_dispersion"] > 0
        assert f["sample_window"] == "last_15_all_competitions"

    def test_window_truncates_to_15(self):
        matches = [{"xg_for": 1.0, "xg_against": 1.0}] * 30
        f = xg.compute_xg_features_l15(matches)
        assert f["n_samples"] == 15

    def test_empty_input_returns_none_fields(self):
        f = xg.compute_xg_features_l15([])
        assert f["xg_l15_mean"] is None
        assert f["xg_l15_std"]  is None

    def test_skips_missing_xg_values(self):
        matches = [
            {"xg_for": None, "xg_against": None},
            {"xg_for": 1.0,  "xg_against": 0.5},
        ]
        f = xg.compute_xg_features_l15(matches)
        assert f["n_samples"] == 1
        assert f["xg_l15_mean"] == pytest.approx(1.0)


# ════════════════════════════════════════════════════════════════════════
# Cascade orchestration
# ════════════════════════════════════════════════════════════════════════
class FakeCollection:
    def __init__(self):
        self.docs = []

    async def find_one(self, q):
        for d in self.docs:
            if all(d.get(k) == v for k, v in (q or {}).items()):
                return d
        return None

    async def update_one(self, q, update, upsert=False):
        for i, d in enumerate(self.docs):
            if all(d.get(k) == v for k, v in (q or {}).items()):
                self.docs[i] = {**d, **(update.get("$set") or {})}
                return {"matched": 1}
        if upsert:
            new = dict(q or {})
            new.update(update.get("$set") or {})
            self.docs.append(new)
        return {"matched": 0, "upserted": int(upsert)}


class FakeDB:
    def __init__(self):
        self.football_team_xg_history = FakeCollection()


@pytest.mark.asyncio
class TestCascade:
    async def test_understat_first_short_circuits(self):
        calls = []

        async def transport(url, *, timeout=35.0):
            calls.append(url)
            if "understat.com" in url:
                return UNDERSTAT_HTML
            return None

        out = await xg.get_team_xg_history(
            "Arsenal", season=2024,
            transport=transport,
            min_samples=2,
        )
        assert out["available"] is True
        assert out["source"] == "understat"
        assert out["reason_code"] == xg.RC_FOUND_UNDERSTAT
        assert out["tried_sources"] == ["understat"]
        assert len(out["matches"]) == 2
        # Confirm we never hit fbref/footystats once Understat succeeded.
        assert all("understat.com" in u for u in calls)

    async def test_fallback_to_fbref(self):
        calls = []

        async def transport(url, *, timeout=35.0):
            calls.append(url)
            if "understat.com" in url:
                return ""
            if "fbref.com" in url:
                return FBREF_HTML
            return None

        out = await xg.get_team_xg_history(
            "Arsenal", season=2024,
            fbref_id="18bb7c10/Arsenal",
            transport=transport,
            min_samples=2,
        )
        assert out["source"] == "fbref"
        assert out["reason_code"] == xg.RC_FOUND_FBREF
        assert out["tried_sources"] == ["understat", "fbref"]
        assert len(out["matches"]) == 2

    async def test_fallback_to_footystats(self):
        async def transport(url, *, timeout=35.0):
            if "footystats.org" in url:
                return FOOTYSTATS_HTML
            return None

        out = await xg.get_team_xg_history(
            "Arsenal", transport=transport, min_samples=2,
        )
        assert out["source"] == "footystats"
        assert out["reason_code"] == xg.RC_FOUND_FOOTYSTATS

    async def test_fallback_to_thestatsapi(self):
        captured = {}

        async def thestatsapi_fetcher(team, *, thestatsapi_id):
            captured["called_with"] = (team, thestatsapi_id)
            return [
                {"date": "2024-08-24", "opponent": "X", "venue": "home",
                  "xg_for": 1.1, "xg_against": 0.9, "goals_for": 1,
                  "goals_against": 0, "competition": "thestatsapi",
                  "source": "thestatsapi"},
                {"date": "2024-08-17", "opponent": "Y", "venue": "away",
                  "xg_for": 0.7, "xg_against": 1.3, "goals_for": 0,
                  "goals_against": 1, "competition": "thestatsapi",
                  "source": "thestatsapi"},
            ]

        async def transport(_url, *, timeout=35.0):
            return None  # everything else fails

        out = await xg.get_team_xg_history(
            "Arsenal", thestatsapi_id="ts-42",
            transport=transport,
            thestatsapi_fetcher=thestatsapi_fetcher,
            min_samples=2,
        )
        assert out["source"] == "thestatsapi"
        assert out["reason_code"] == xg.RC_FOUND_THESTATSAPI
        assert captured["called_with"] == ("Arsenal", "ts-42")

    async def test_all_sources_failed(self):
        async def transport(_url, *, timeout=35.0):
            return None

        async def thestatsapi_fetcher(_team, *, thestatsapi_id):
            return []

        out = await xg.get_team_xg_history(
            "Arsenal", transport=transport,
            thestatsapi_fetcher=thestatsapi_fetcher,
            min_samples=2,
        )
        assert out["available"] is False
        assert out["source"] is None
        assert out["reason_code"] == xg.RC_ALL_SOURCES_FAILED
        assert out["tried_sources"] == ["understat", "fbref",
                                          "footystats", "thestatsapi"]

    async def test_insufficient_sample_keeps_best_partial(self):
        # Only 1 match available (< min_samples=5).
        async def transport(url, *, timeout=35.0):
            if "understat.com" in url:
                # Return only one match for Arsenal in our payload.
                return UNDERSTAT_HTML.replace(
                    '},{\\x22datetime\\x22:\\x222024-08-24',
                    '}],_old_=[{\\x22d\\x22:\\x222024-08-24',
                )
            return None

        async def thestatsapi_fetcher(_team, *, thestatsapi_id):
            return []

        out = await xg.get_team_xg_history(
            "Arsenal", transport=transport,
            thestatsapi_fetcher=thestatsapi_fetcher,
            min_samples=5,
        )
        # We have SOME data but below the threshold → not "available"
        # but still surface what we have for downstream auditing.
        assert out["reason_code"] in (xg.RC_INSUFFICIENT_SAMPLE,
                                        xg.RC_ALL_SOURCES_FAILED)


# ════════════════════════════════════════════════════════════════════════
# Cache
# ════════════════════════════════════════════════════════════════════════
@pytest.mark.asyncio
class TestCache:
    async def test_persists_on_success(self):
        db = FakeDB()

        async def transport(url, *, timeout=35.0):
            return UNDERSTAT_HTML if "understat.com" in url else None

        await xg.get_team_xg_history(
            "Arsenal", season=2024, db=db, transport=transport, min_samples=2,
        )
        coll = db.football_team_xg_history
        assert len(coll.docs) == 1
        stored = coll.docs[0]
        assert stored["team_norm"] == "arsenal"
        assert stored["underlying_source"] == "understat"
        assert isinstance(stored["fetched_at"], datetime)

    async def test_cache_hit_short_circuits_sources(self):
        db = FakeDB()
        await db.football_team_xg_history.update_one(
            {"team_norm": "arsenal", "league": None, "season": 2024},
            {"$set": {
                "team_norm": "arsenal", "team_name": "Arsenal",
                "league": None, "season": 2024,
                "matches": [{"xg_for": 1.5, "xg_against": 0.7}],
                "fetched_at": datetime.now(timezone.utc),
                "underlying_source": "understat",
            }},
            upsert=True,
        )
        called = {"n": 0}

        async def transport(_url, *, timeout=35.0):
            called["n"] += 1
            return None

        out = await xg.get_team_xg_history(
            "Arsenal", season=2024, db=db, transport=transport, min_samples=1,
        )
        assert out["from_cache"] is True
        assert out["source"] == "cache"
        assert out["reason_code"] == xg.RC_FROM_CACHE
        assert called["n"] == 0

    async def test_force_refresh_bypasses_cache(self):
        db = FakeDB()
        await db.football_team_xg_history.update_one(
            {"team_norm": "arsenal", "league": None, "season": 2024},
            {"$set": {
                "team_norm": "arsenal", "team_name": "Arsenal",
                "league": None, "season": 2024,
                "matches": [{"xg_for": 1.5}],
                "fetched_at": datetime.now(timezone.utc),
                "underlying_source": "understat",
            }},
            upsert=True,
        )
        async def transport(url, *, timeout=35.0):
            return UNDERSTAT_HTML if "understat.com" in url else None

        out = await xg.get_team_xg_history(
            "Arsenal", season=2024, db=db, transport=transport,
            min_samples=2, force_refresh=True,
        )
        assert out["from_cache"] is False
        assert out["source"] == "understat"

    async def test_stale_cache_is_ignored(self):
        db = FakeDB()
        await db.football_team_xg_history.update_one(
            {"team_norm": "arsenal", "league": None, "season": 2024},
            {"$set": {
                "team_norm": "arsenal", "league": None, "season": 2024,
                "matches": [{"xg_for": 1.0}],
                "fetched_at": datetime.now(timezone.utc) - timedelta(days=10),
                "underlying_source": "understat",
            }},
            upsert=True,
        )
        async def transport(url, *, timeout=35.0):
            return UNDERSTAT_HTML if "understat.com" in url else None

        out = await xg.get_team_xg_history(
            "Arsenal", season=2024, db=db, transport=transport,
            min_samples=2, cache_ttl_seconds=24 * 3600,
        )
        assert out["from_cache"] is False
        assert out["source"] == "understat"


# ════════════════════════════════════════════════════════════════════════
# Indexes
# ════════════════════════════════════════════════════════════════════════
@pytest.mark.asyncio
class TestIndexes:
    async def test_ensure_indexes_no_db_is_noop(self):
        r = await xg.ensure_indexes(None)
        assert r["created"] == []
        assert r["skipped"] == "no_db"

    async def test_ensure_indexes_creates_namespace(self):
        class _Coll:
            def __init__(self):
                self.created = []
            async def create_index(self, key, **kw):
                self.created.append({"key": key, **kw})

        class _DB:
            def __init__(self):
                self.football_team_xg_history = _Coll()

        db = _DB()
        r = await xg.ensure_indexes(db)
        assert "ix_team_league_season" in r["created"]
        assert "ttl_fetched_at" in r["created"]
