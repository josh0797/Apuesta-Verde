"""Sprint E.1.1-d/-f · Tests for the auto-resolver scheduler and the
365Scores Top Trends client.

Pure helpers are exercised first (no Mongo, no HTTP); the async tests
use the same FakeDB pattern as ``test_live_odds_monitor.py``.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from unittest.mock import patch

import pytest

from services import market_identity_auto_resolver as auto_res
from services.external_sources import score365_trends_client as s365


# ════════════════════════════════════════════════════════════════════════
# Fake Mongo
# ════════════════════════════════════════════════════════════════════════
class _AsyncCursor:
    def __init__(self, docs):
        self.docs = list(docs)

    def sort(self, *args, **kwargs):
        return self

    def limit(self, n):
        self.docs = self.docs[: int(n)]
        return self

    async def to_list(self, length=None):
        return list(self.docs)

    def __aiter__(self):
        self._it = iter(self.docs)
        return self

    async def __anext__(self):
        try:
            return next(self._it)
        except StopIteration:
            raise StopAsyncIteration


class FakeCollection:
    def __init__(self, docs=None):
        self.docs: list[dict] = list(docs or [])

    def find(self, query=None, projection=None, sort=None, limit=None):
        q = query or {}

        def _match(d):
            for k, v in q.items():
                if isinstance(v, dict) and "$gte" in v:
                    cur = d.get(k)
                    if cur is None or cur < v["$gte"]:
                        return False
                else:
                    if d.get(k) != v:
                        return False
            return True

        return _AsyncCursor([d for d in self.docs if _match(d)])

    async def find_one(self, query=None, projection=None, sort=None):
        async for d in self.find(query or {}):
            return d
        return None

    async def insert_one(self, doc):
        self.docs.append(doc)


class FakeDB:
    def __init__(self):
        self.pick_runs = FakeCollection()
        self.picks     = FakeCollection()
        self.odds_event_id_mappings      = FakeCollection()
        self.market_identity_resolutions = FakeCollection()


# ════════════════════════════════════════════════════════════════════════
# Auto-resolver — pure
# ════════════════════════════════════════════════════════════════════════
class TestExtractPendingIdentities:
    def test_extracts_state_top_level(self):
        runs = [{
            "sport":   "football",
            "payload": {"picks": [{
                "match_id": "m1", "home_team": "Portugal",
                "away_team": "Congo DR",
                "state":    "REQUIRES_MARKET_IDENTIFICATION",
                "odds":     1.25,
            }]},
        }]
        out = auto_res.extract_pending_identities(run_docs=runs)
        assert len(out) == 1
        assert out[0]["match_id"]       == "m1"
        assert out[0]["detected_price"] == 1.25

    def test_extracts_state_nested_in_market_trace(self):
        runs = [{
            "sport":   "football",
            "payload": {"discarded_market": [{
                "match_id": "m2", "home_team": "H", "away_team": "A",
                "market_trace": {
                    "state":         "REQUIRES_MARKET_IDENTIFICATION",
                    "odds_visible":  1.30,
                },
            }]},
        }]
        out = auto_res.extract_pending_identities(run_docs=runs)
        assert len(out) == 1
        assert out[0]["match_id"]       == "m2"
        assert out[0]["detected_price"] == 1.30

    def test_skips_entries_without_required_state(self):
        runs = [{
            "sport":   "football",
            "payload": {"picks": [
                {"match_id": "m1", "state": "VALID_PICK", "odds": 1.5},
                {"match_id": "m2", "state": "REQUIRES_MARKET_IDENTIFICATION",
                 "odds": 1.25},
            ]},
        }]
        out = auto_res.extract_pending_identities(run_docs=runs)
        assert [x["match_id"] for x in out] == ["m2"]

    def test_dedupes_across_buckets(self):
        runs = [{
            "sport":   "football",
            "payload": {
                "picks": [{
                    "match_id": "m1", "state": "REQUIRES_MARKET_IDENTIFICATION",
                    "odds": 1.25,
                }],
                "discarded_market": [{
                    "match_id": "m1", "state": "REQUIRES_MARKET_IDENTIFICATION",
                    "odds": 1.25,
                }],
            },
        }]
        out = auto_res.extract_pending_identities(run_docs=runs)
        assert len(out) == 1

    def test_already_resolved_keys_are_skipped(self):
        runs = [{
            "sport":   "football",
            "payload": {"picks": [{
                "match_id": "m1", "state": "REQUIRES_MARKET_IDENTIFICATION",
                "odds": 1.25,
            }]},
        }]
        out = auto_res.extract_pending_identities(
            run_docs=runs,
            already_resolved_keys={("m1", 1.25)},
        )
        assert out == []

    def test_invalid_prices_are_skipped(self):
        runs = [{
            "sport":   "football",
            "payload": {"picks": [
                {"match_id": "m1", "state": "REQUIRES_MARKET_IDENTIFICATION",
                 "odds": "garbage"},
                {"match_id": "m2", "state": "REQUIRES_MARKET_IDENTIFICATION",
                 "odds": 0.95},
            ]},
        }]
        out = auto_res.extract_pending_identities(run_docs=runs)
        assert out == []

    def test_sport_filter(self):
        runs = [
            {"sport": "baseball", "payload": {"picks": [{
                "match_id": "b1", "state": "REQUIRES_MARKET_IDENTIFICATION",
                "odds": 1.25,
            }]}},
            {"sport": "football", "payload": {"picks": [{
                "match_id": "f1", "state": "REQUIRES_MARKET_IDENTIFICATION",
                "odds": 1.25,
            }]}},
        ]
        out = auto_res.extract_pending_identities(
            run_docs=runs, sport_filter="football",
        )
        assert [x["match_id"] for x in out] == ["f1"]


# ════════════════════════════════════════════════════════════════════════
# Auto-resolver — async cycle
# ════════════════════════════════════════════════════════════════════════
@pytest.mark.asyncio
async def test_run_cycle_disabled(monkeypatch):
    monkeypatch.setenv("IDENTITY_RESOLVER_ENABLED", "false")
    db = FakeDB()
    report = await auto_res.run_cycle(db)
    assert report["enabled"] is False
    assert "DISABLED" in report["reasons"]


@pytest.mark.asyncio
async def test_run_cycle_no_runs(monkeypatch):
    monkeypatch.setenv("IDENTITY_RESOLVER_ENABLED", "true")
    db = FakeDB()
    report = await auto_res.run_cycle(db)
    assert report["enabled"] is True
    assert "EMPTY_RUNS" in report["reasons"]


@pytest.mark.asyncio
async def test_run_cycle_calls_resolver(monkeypatch):
    monkeypatch.setenv("IDENTITY_RESOLVER_ENABLED", "true")
    db = FakeDB()
    db.pick_runs.docs.append({
        "sport": "football",
        "generated_at": datetime.now(timezone.utc),
        "payload": {"picks": [{
            "match_id": "m_pt_cd",
            "home_team": "Portugal", "away_team": "Congo DR",
            "state": "REQUIRES_MARKET_IDENTIFICATION",
            "odds":  1.25,
        }]},
    })

    calls: list[dict] = []

    async def _fake_resolve(db, *, match, detected_price, **kwargs):
        calls.append({"match": match, "price": detected_price})
        return {"resolution_status": "RESOLVED"}

    with patch.object(auto_res.mir, "resolve_market_identity", _fake_resolve):
        report = await auto_res.run_cycle(db)
    assert report["resolutions_run"] == 1
    assert report["resolutions_ok"]  == 1
    assert calls and calls[0]["match"]["match_id"] == "m_pt_cd"
    assert calls[0]["price"] == 1.25


@pytest.mark.asyncio
async def test_run_cycle_respects_max_per_cycle(monkeypatch):
    monkeypatch.setenv("IDENTITY_RESOLVER_ENABLED", "true")
    monkeypatch.setenv("IDENTITY_RESOLVER_MAX_PER_CYCLE", "2")
    db = FakeDB()
    db.pick_runs.docs.append({
        "sport": "football",
        "generated_at": datetime.now(timezone.utc),
        "payload": {"picks": [
            {"match_id": f"m{i}", "home_team": "A", "away_team": "B",
             "state": "REQUIRES_MARKET_IDENTIFICATION", "odds": 1.20 + i*0.01}
            for i in range(5)
        ]},
    })

    counter = {"n": 0}

    async def _ok(db, *, match, detected_price, **kwargs):
        counter["n"] += 1
        return {"resolution_status": "RESOLVED"}

    with patch.object(auto_res.mir, "resolve_market_identity", _ok):
        report = await auto_res.run_cycle(db)
    assert counter["n"] == 2
    assert report["resolutions_run"] == 2


# ════════════════════════════════════════════════════════════════════════
# 365Scores Top Trends — pure parser
# ════════════════════════════════════════════════════════════════════════
class TestParseTrendText:
    HOME = "Portugal"
    AWAY = "Congo DR"

    def _parse(self, text):
        return s365.parse_trend_text(
            text=text, home_team=self.HOME, away_team=self.AWAY,
        )

    def test_win_with_sample(self):
        out = self._parse("Portugal ganó 4/5 últimos partidos")
        assert out["trend_type"] == "WIN"
        assert out["team"] == "Portugal"
        assert out["team_side"] == "home"
        assert out["value"] == "4/5"
        assert out["sample"]["hits"] == 4 and out["sample"]["total"] == 5
        assert out["period"] == "last_5_matches"
        assert out["scope"] == "all"
        assert out["confidence"] in ("HIGH", "MEDIUM", "LOW")

    def test_under_as_visitor(self):
        out = self._parse("RD Congo Menos de 2.5 goles como visitante 14/16 últimos partidos")
        assert out["trend_type"] == "UNDER_2.5"
        # "Congo DR" / "RD Congo" — token-only fallback may flag UNKNOWN,
        # so we test it's at least the away or UNKNOWN side.
        assert out["team_side"] in ("away", "UNKNOWN")
        assert out["scope"] == "away"
        assert out["value"] == "14/16"
        assert out["sample"]["hits"] == 14 and out["sample"]["total"] == 16
        assert out["period"] == "last_16_matches"

    def test_over_2_5_in_home(self):
        out = self._parse("Portugal Más de 2.5 goles como local 8/10")
        assert out["trend_type"] == "OVER_2.5"
        assert out["scope"] == "home"
        assert out["sample"]["hits"] == 8

    def test_lost(self):
        out = self._parse("RD Congo perdió 3/4 últimos partidos")
        assert out["trend_type"] == "LOSE"
        assert out["value"] == "3/4"

    def test_btts(self):
        out = self._parse("Ambos equipos marcan 7/10 últimos partidos")
        assert out["trend_type"] == "BTTS_YES"
        assert out["sample"]["total"] == 10

    def test_clean_sheet(self):
        out = self._parse("Portugal portería a cero 5/8 partidos")
        assert out["trend_type"] == "CLEAN_SHEET"

    def test_unknown_falls_back_to_raw(self):
        out = self._parse("Texto raro sin patrón conocido 1/2")
        assert out["trend_type"] == "RAW"
        assert out["sample"]["hits"] == 1

    def test_empty_returns_none(self):
        assert s365.parse_trend_text(text="") is None
        assert s365.parse_trend_text(text=None) is None  # type: ignore


class TestParseTrendsList:
    def test_mixed_strings_and_dicts(self):
        items = [
            "Portugal ganó 4/5 últimos partidos",
            {"text": "RD Congo Menos de 2.5 goles 14/16"},
            42,         # noise — skipped
            None,
            {"description": "Ambos equipos marcan 7/10"},
        ]
        rows = s365.parse_trends_list(
            raw_items=items, home_team="Portugal", away_team="Congo DR",
        )
        assert len(rows) == 3
        types = {r["trend_type"] for r in rows}
        assert "WIN"        in types
        assert "UNDER_2.5"  in types
        assert "BTTS_YES"   in types


class TestExtractTrendStringsFromJson:
    def test_finds_top_trends_top_level(self):
        payload = {
            "topTrends": [
                "Portugal ganó 4/5",
                "RD Congo Menos de 2.5 goles 14/16",
            ],
        }
        out = s365._extract_trend_strings_from_json(payload)
        assert len(out) == 2

    def test_finds_trends_nested(self):
        payload = {"game": {"data": {"trends": [
            {"text": "Portugal ganó 4/5"},
            {"text": "RD Congo Menos de 2.5 goles 14/16"},
        ]}}}
        out = s365._extract_trend_strings_from_json(payload)
        assert len(out) == 2

    def test_returns_empty_on_no_trends_key(self):
        payload = {"unrelated": [1, 2, 3]}
        out = s365._extract_trend_strings_from_json(payload)
        assert out == []

    def test_dedupes(self):
        payload = {"topTrends": ["dup", "dup", "unique"]}
        out = s365._extract_trend_strings_from_json(payload)
        assert out == ["dup", "unique"]


# ════════════════════════════════════════════════════════════════════════
# 365Scores Top Trends — async fetch_top_trends
# ════════════════════════════════════════════════════════════════════════
@pytest.mark.asyncio
async def test_fetch_top_trends_no_game_id():
    out = await s365.fetch_top_trends({}, home_team="A", away_team="B")
    assert out["available"] is False
    assert out["reason_code"] == "SCORE365_ID_MISSING"
    assert out["trends"] == []


@pytest.mark.asyncio
async def test_fetch_top_trends_success_via_injected_fetcher():
    match_doc = {
        "external_ids": {"365scores": {"game_id": "4697734"}},
        "home_team": "Portugal", "away_team": "Congo DR",
    }

    async def _fetcher(url):
        return {
            "available": True, "stage": "FETCH_TRENDS",
            "json": {"topTrends": [
                "Portugal ganó 4/5 últimos partidos",
                "RD Congo Menos de 2.5 goles como visitante 14/16",
            ]},
            "target_url": url, "fetched_at": "2026-06-17T00:00:00Z",
            "provider": "365scores", "transport": "scrape_do",
            "source": "365scores_top_trends",
        }

    out = await s365.fetch_top_trends(match_doc, fetcher=_fetcher)
    assert out["available"] is True
    assert out["reason_code"] == "TOP_TRENDS_FROM_365SCORES"
    assert out["trends_count"] == 2
    types = {t["trend_type"] for t in out["trends"]}
    assert types == {"WIN", "UNDER_2.5"}


@pytest.mark.asyncio
async def test_fetch_top_trends_all_endpoints_fail():
    match_doc = {
        "external_ids": {"365scores": {"game_id": "4697734"}},
        "home_team": "Portugal", "away_team": "Congo DR",
    }

    async def _fetcher(url):
        return {
            "available": False, "stage": "FETCH_TRENDS",
            "reason_code": "SCORE365_BLOCKED_OR_FORBIDDEN",
            "message_user": "blocked",
            "provider": "365scores", "transport": "scrape_do",
            "source": "365scores_top_trends", "target_url": url,
        }

    out = await s365.fetch_top_trends(match_doc, fetcher=_fetcher)
    assert out["available"] is False
    assert out["trends"] == []
    assert "reason_code" in out


@pytest.mark.asyncio
async def test_fetch_top_trends_json_without_trends_key():
    match_doc = {
        "external_ids": {"365scores": {"game_id": "4697734"}},
        "home_team": "A", "away_team": "B",
    }

    async def _fetcher(url):
        return {
            "available": True, "stage": "FETCH_TRENDS",
            "json": {"unrelated": [{"foo": "bar"}]},
            "target_url": url, "fetched_at": "x",
            "provider": "365scores", "transport": "scrape_do",
            "source": "365scores_top_trends",
        }

    out = await s365.fetch_top_trends(match_doc, fetcher=_fetcher)
    assert out["available"] is False
    assert out["reason_code"] == "SCORE365_TOP_TRENDS_NOT_FOUND"


# ════════════════════════════════════════════════════════════════════════
# Orchestrator wiring — sportytrader replaced by score365_trends
# ════════════════════════════════════════════════════════════════════════
@pytest.mark.asyncio
async def test_orchestrator_replaces_sportytrader_with_score365_trends():
    from services import football_external_fallback_orchestrator as orch

    async def _fake_editorial(match):
        return {
            "available": True, "reason_codes": [],
            "home_team": "Portugal", "away_team": "Congo DR",
            "sportytrader": {"available": True, "raw": "legacy"},
            "forebet":      {"predicted_score": "2-0",
                              "forebet_pct_1": 65, "expected_goals": 2.1},
        }

    async def _fake_trends(match_payload, **kwargs):
        return {
            "available": True, "reason_code": "TOP_TRENDS_FROM_365SCORES",
            "provider": "365scores", "source": "365scores_top_trends",
            "trends": [
                {"raw": "Portugal ganó 4/5 últimos partidos",
                 "trend_type": "WIN", "team_side": "home"},
            ],
            "trends_count": 1,
        }

    with patch(
        "services.external_editorial_provider.fetch_external_editorial_for_match",
        _fake_editorial,
    ), patch(
        "services.external_sources.score365_trends_client.fetch_top_trends",
        _fake_trends,
    ):
        out = await orch.build_external_fallback_context({
            "match_id": "x", "home_team": "Portugal", "away_team": "Congo DR",
        })

    # The replacement annotation is present.
    assert out["sportytrader"]["available"] is False
    assert out["sportytrader"]["deprecated"] is True
    assert out["sportytrader"]["replaced_by"] == "score365_trends"
    # Trends block is wired.
    assert out["top_trends"]["available"] is True
    assert out["top_trends"]["trends_count"] == 1
    # The legacy "sportytrader" source-used flag has been removed.
    assert "sportytrader" not in out["sources_used"]
    assert "score365_trends" in out["sources_used"]
    assert "TOP_TRENDS_FROM_365SCORES" in out["reason_codes"]
