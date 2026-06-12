"""Phase F62 — Football Discarded Match Scores24 Review smoke tests.

Covers:
  * Slug builder happy path + variants + missing fields.
  * Decision logic for all 3 outcomes (CONFIRM_DISCARD, MOVE_TO_WATCHLIST,
    RESCUE_ALTERNATIVE_MARKET).
  * Per-run quota gating (MAX_PER_RUN).
  * Cache flow with an in-memory fake db.
  * Env kill-switch.
  * Fail-soft on scraper error / no URL candidates.
"""
from __future__ import annotations

import asyncio
import os
from datetime import datetime, timedelta, timezone

import pytest

from services.football_discarded_scores24_review import (
    DECISION_CONFIRM_DISCARD,
    DECISION_MOVE_TO_WATCHLIST,
    DECISION_RESCUE_ALT_MARKET,
    ENGINE_VERSION,
    RC_DISABLED_BY_ENV,
    RC_FETCH_FAILED,
    RC_FROM_CACHE,
    RC_QUOTA_RUN_EXCEEDED,
    RC_RESCUED_CORNERS,
    RC_URL_NOT_RESOLVED,
    RC_USED,
    build_scores24_slug_candidates,
    make_run_counter,
    review_discarded_match_with_scores24,
)


# ─────────────────────────────────────────────────────────────────────
# Fake Mongo (in-memory) — supports the 3 motor calls we make.
# ─────────────────────────────────────────────────────────────────────
class _FakeColl:
    def __init__(self):
        self._docs: dict[str, dict] = {}

    async def find_one(self, query):
        return self._docs.get(query["_id"])

    async def update_one(self, query, update, upsert=False):
        _id = query["_id"]
        doc = self._docs.get(_id, {})
        if "$set" in update:
            doc.update(update["$set"])
        if "$inc" in update:
            for k, v in update["$inc"].items():
                doc[k] = (doc.get(k) or 0) + v
        if upsert or _id in self._docs:
            self._docs[_id] = doc

    async def find_one_and_update(self, query, update, upsert=False, return_document=False):
        _id = query["_id"]
        doc = self._docs.get(_id, {"_id": _id})
        if "$set" in update:
            doc.update(update["$set"])
        if "$inc" in update:
            for k, v in update["$inc"].items():
                doc[k] = (doc.get(k) or 0) + v
        self._docs[_id] = doc
        return doc if return_document else self._docs.get(_id)


class _FakeDB:
    def __init__(self):
        self._colls: dict[str, _FakeColl] = {}

    def __getitem__(self, name):
        if name not in self._colls:
            self._colls[name] = _FakeColl()
        return self._colls[name]


# ─────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────
def _base_match():
    return {
        "match_id":     "fixture-12345",
        "match_date":   "2026-06-11",
        "home_team":    {"name": "México"},
        "away_team":    {"name": "South Africa"},
        "competition":  "Friendlies",
        "status":       "scheduled",
    }


async def _scrape_corners_under():
    async def _scrape(*, url, **_):
        return {
            "available": True,
            "sections": [{
                "section":  "corners_prediction",
                "title":    "Predicción sobre córners",
                "text":     "menos de 9.5 córners totales cuota 1.58",
                "recommended_market": "Under 9.5 córners",
                "market_type": "corners_total",
                "side": "UNDER",
                "line": 9.5,
                "odds": 1.58,
                "reason_codes": ["SCORES24_CORNERS_PREDICTION_FOUND"],
            }],
            "consensus": {
                "primary_section": "corners_prediction",
                "primary_market":  "Under 9.5 córners",
                "primary_market_type": "corners_total",
                "primary_side":    "UNDER",
                "primary_line":    9.5,
                "primary_odds":    1.58,
            },
            "reason_codes": ["SCORES24_FETCH_OK"],
        }
    return _scrape


async def _scrape_editorial_only():
    async def _scrape(*, url, **_):
        return {
            "available": True,
            "sections": [{
                "section":  "prediccion_redaccion",
                "title":    "Predicción de la redacción",
                "text":     "Hándicap (-1) cuota 1.72",
                "recommended_market": "Hándicap (-1)",
                "market_type": "handicap",
                "side": None,
                "line": -1.0,
                "odds": 1.72,
                "narrative_context": "México llega como favorito clarísimo",
                "reason_codes": ["SCORES24_HANDICAP_FOUND"],
            }],
            "consensus": {
                "primary_section": "prediccion_redaccion",
                "primary_market":  "Hándicap (-1)",
                "primary_market_type": "handicap",
                "primary_side":    None,
                "primary_line":    -1.0,
                "primary_odds":    1.72,
            },
            "reason_codes": ["SCORES24_FETCH_OK"],
        }
    return _scrape


async def _scrape_empty():
    async def _scrape(*, url, **_):
        return {
            "available": True,
            "sections": [],
            "consensus": {},
            "reason_codes": ["SCORES24_FETCH_OK"],
        }
    return _scrape


async def _scrape_unavailable():
    async def _scrape(*, url, **_):
        return {"available": False, "reason_codes": ["SCORES24_FETCH_FAILED"]}
    return _scrape


# ─────────────────────────────────────────────────────────────────────
# Slug builder
# ─────────────────────────────────────────────────────────────────────
def test_slug_builder_happy_path():
    urls = build_scores24_slug_candidates(_base_match())
    assert len(urls) == 2
    assert urls[0] == "https://scores24.live/es/soccer/m-11-06-2026-mexico-south-africa-prediction"
    # 2nd is the swap.
    assert "south-africa-mexico" in urls[1]


def test_slug_builder_handles_accents_and_spaces():
    m = {
        "match_id":   "x",
        "match_date": "2026-06-11",
        "home_team":  {"name": "São Paulo FC"},
        "away_team":  {"name": "Atlético Madrid"},
    }
    urls = build_scores24_slug_candidates(m)
    assert urls
    # Accents stripped, hyphenated, no underscores.
    assert "sao-paulo-fc-atletico-madrid" in urls[0]


def test_slug_builder_respects_explicit_url():
    m = _base_match()
    m["scores24_url"] = "https://scores24.live/es/soccer/m-custom-url-prediction"
    urls = build_scores24_slug_candidates(m)
    assert urls == ["https://scores24.live/es/soccer/m-custom-url-prediction"]


def test_slug_builder_returns_empty_on_missing_fields():
    assert build_scores24_slug_candidates(None) == []
    assert build_scores24_slug_candidates({}) == []
    # Missing date.
    assert build_scores24_slug_candidates({
        "home_team": {"name": "A"}, "away_team": {"name": "B"},
    }) == []
    # Missing team.
    assert build_scores24_slug_candidates({
        "home_team": {"name": "A"}, "match_date": "2026-01-01",
    }) == []


def test_slug_builder_accepts_unix_timestamp():
    m = {
        "match_id": "x", "home_team": {"name": "A"}, "away_team": {"name": "B"},
        "kickoff": 1781913600,  # 2026-06-19
    }
    urls = build_scores24_slug_candidates(m)
    assert urls
    assert "-2026-" in urls[0]


# ─────────────────────────────────────────────────────────────────────
# Decision: RESCUE (corners present)
# ─────────────────────────────────────────────────────────────────────
def test_decision_rescue_when_corners_prediction_present():
    db = _FakeDB()
    scrape = asyncio.run(_scrape_corners_under())
    out = asyncio.run(review_discarded_match_with_scores24(
        _base_match(), db=db, scrape_fn=scrape, discard_reason="edge_insufficient",
    ))
    assert out["available"] is True
    assert out["decision"] == DECISION_RESCUE_ALT_MARKET
    assert out["rescued_market"] == {
        "market_family": "CORNERS",
        "market":        "Under 9.5 córners",
        "side":          "UNDER",
        "line":          9.5,
        "odds":          1.58,
    }
    assert out["corners_prediction"]["available"] is True
    assert RC_USED in out["reason_codes"]
    assert RC_RESCUED_CORNERS in out["reason_codes"]
    assert out["original_discard_reason"] == "edge_insufficient"
    assert out["url_used"].startswith("https://scores24.live")


# ─────────────────────────────────────────────────────────────────────
# Decision: WATCHLIST (editorial only, no corners)
# ─────────────────────────────────────────────────────────────────────
def test_decision_watchlist_when_editorial_only():
    db = _FakeDB()
    scrape = asyncio.run(_scrape_editorial_only())
    out = asyncio.run(review_discarded_match_with_scores24(
        _base_match(), db=db, scrape_fn=scrape,
    ))
    assert out["decision"] == DECISION_MOVE_TO_WATCHLIST
    assert out["editorial_prediction"]["available"] is True
    assert out["editorial_prediction"]["market"] == "Hándicap (-1)"
    assert out["corners_prediction"]["available"] is False
    assert out["rescued_market"] is None


# ─────────────────────────────────────────────────────────────────────
# Decision: CONFIRM_DISCARD
# ─────────────────────────────────────────────────────────────────────
def test_decision_confirm_when_empty_scrape():
    db = _FakeDB()
    scrape = asyncio.run(_scrape_empty())
    out = asyncio.run(review_discarded_match_with_scores24(
        _base_match(), db=db, scrape_fn=scrape,
    ))
    assert out["decision"] == DECISION_CONFIRM_DISCARD
    assert out["available"] is True
    assert out["external_context_found"] is False


def test_decision_confirm_when_scraper_returns_unavailable():
    db = _FakeDB()
    scrape = asyncio.run(_scrape_unavailable())
    out = asyncio.run(review_discarded_match_with_scores24(
        _base_match(), db=db, scrape_fn=scrape,
    ))
    assert out["decision"] == DECISION_CONFIRM_DISCARD
    assert out["available"] is False
    assert RC_FETCH_FAILED in out["reason_codes"]
    assert out["url_tried"].startswith("https://scores24.live")


# ─────────────────────────────────────────────────────────────────────
# Env kill-switch
# ─────────────────────────────────────────────────────────────────────
def test_env_kill_switch_disables_review(monkeypatch):
    monkeypatch.setenv("SCORES24_DISCARDED_REVIEW_ENABLED", "false")
    out = asyncio.run(review_discarded_match_with_scores24(_base_match()))
    assert out["available"] is False
    assert RC_DISABLED_BY_ENV in out["reason_codes"]


# ─────────────────────────────────────────────────────────────────────
# URL not resolvable
# ─────────────────────────────────────────────────────────────────────
def test_no_slug_candidates_short_circuits():
    db = _FakeDB()
    # No date → builder returns [] → review short-circuits.
    out = asyncio.run(review_discarded_match_with_scores24(
        {"match_id": "x", "home_team": {"name": "A"}, "away_team": {"name": "B"}},
        db=db, scrape_fn=None,
    ))
    assert out["available"] is False
    assert RC_URL_NOT_RESOLVED in out["reason_codes"]


# ─────────────────────────────────────────────────────────────────────
# Per-run quota
# ─────────────────────────────────────────────────────────────────────
def test_per_run_counter_blocks_after_limit():
    counter = make_run_counter(limit=2)
    db = _FakeDB()
    scrape = asyncio.run(_scrape_empty())
    # 1st call: consumes slot 1.
    out1 = asyncio.run(review_discarded_match_with_scores24(
        _base_match(), db=db, scrape_fn=scrape, run_counter=counter,
    ))
    assert out1["available"] is True
    # 2nd call: consumes slot 2.
    m2 = _base_match(); m2["match_id"] = "m2"
    out2 = asyncio.run(review_discarded_match_with_scores24(
        m2, db=db, scrape_fn=scrape, run_counter=counter,
    ))
    assert out2["available"] is True
    # 3rd call: blocked.
    m3 = _base_match(); m3["match_id"] = "m3"
    out3 = asyncio.run(review_discarded_match_with_scores24(
        m3, db=db, scrape_fn=scrape, run_counter=counter,
    ))
    assert out3["available"] is False
    assert RC_QUOTA_RUN_EXCEEDED in out3["reason_codes"]
    assert counter.count == 2


# ─────────────────────────────────────────────────────────────────────
# Cache flow
# ─────────────────────────────────────────────────────────────────────
def test_cache_hit_marks_from_cache_and_skips_scraper():
    db = _FakeDB()
    scrape_calls = {"n": 0}

    async def _scrape(*, url, **_):
        scrape_calls["n"] += 1
        return {
            "available": True,
            "sections": [{
                "section": "corners_prediction",
                "recommended_market": "Under 9.5 córners",
                "market_type": "corners_total",
                "side": "UNDER", "line": 9.5, "odds": 1.58,
                "reason_codes": [],
            }],
            "consensus": {
                "primary_market_type": "corners_total",
                "primary_side": "UNDER", "primary_line": 9.5, "primary_odds": 1.58,
                "primary_market": "Under 9.5 córners",
                "primary_section": "corners_prediction",
            },
            "reason_codes": [],
        }

    out1 = asyncio.run(review_discarded_match_with_scores24(
        _base_match(), db=db, scrape_fn=_scrape,
    ))
    assert out1["available"] is True
    assert scrape_calls["n"] == 1
    assert RC_FROM_CACHE not in out1["reason_codes"]

    # Second call → cache hit (same match_id).
    out2 = asyncio.run(review_discarded_match_with_scores24(
        _base_match(), db=db, scrape_fn=_scrape,
    ))
    assert out2["available"] is True
    assert scrape_calls["n"] == 1  # scraper NOT called again.
    assert RC_FROM_CACHE in out2["reason_codes"]
    # Decision is preserved across cache.
    assert out2["decision"] == DECISION_RESCUE_ALT_MARKET


def test_force_bypasses_cache():
    db = _FakeDB()
    scrape_calls = {"n": 0}

    async def _scrape(*, url, **_):
        scrape_calls["n"] += 1
        return {"available": True, "sections": [], "consensus": {}, "reason_codes": []}

    asyncio.run(review_discarded_match_with_scores24(
        _base_match(), db=db, scrape_fn=_scrape,
    ))
    asyncio.run(review_discarded_match_with_scores24(
        _base_match(), db=db, scrape_fn=_scrape, force=True,
    ))
    assert scrape_calls["n"] == 2


# ─────────────────────────────────────────────────────────────────────
# Daily quota
# ─────────────────────────────────────────────────────────────────────
def test_daily_quota_blocks_after_max(monkeypatch):
    monkeypatch.setenv("SCORES24_DISCARDED_MAX_PER_DAY", "2")
    db = _FakeDB()

    async def _scrape(*, url, **_):
        return {"available": True, "sections": [], "consensus": {}, "reason_codes": []}

    # Two allowed.
    for i in range(2):
        m = _base_match()
        m["match_id"] = f"daily-{i}"
        out = asyncio.run(review_discarded_match_with_scores24(
            m, db=db, scrape_fn=_scrape,
        ))
        assert out["available"] is True

    # 3rd blocked by daily quota.
    m3 = _base_match(); m3["match_id"] = "daily-3"
    out3 = asyncio.run(review_discarded_match_with_scores24(
        m3, db=db, scrape_fn=_scrape,
    ))
    assert out3["available"] is False
    assert "SCORES24_QUOTA_DAILY_EXCEEDED" in out3["reason_codes"]


# ─────────────────────────────────────────────────────────────────────
# Sanity: ENGINE_VERSION + shape contract
# ─────────────────────────────────────────────────────────────────────
def test_engine_version_present_in_all_outputs():
    out = asyncio.run(review_discarded_match_with_scores24({}))
    assert out["engine_version"] == ENGINE_VERSION
    assert out["source"] == "scores24"
    assert out["review_type"] == "DISCARDED_MATCH_EXTERNAL_REVIEW"


def test_output_shape_contract_when_rescued():
    db = _FakeDB()
    scrape = asyncio.run(_scrape_corners_under())
    out = asyncio.run(review_discarded_match_with_scores24(
        _base_match(), db=db, scrape_fn=scrape,
    ))
    for k in ("available", "source", "review_type", "original_discard_reason",
              "external_context_found", "decision", "rescued_market",
              "editorial_prediction", "corners_prediction", "reason_codes"):
        assert k in out, f"missing key in output: {k}"
