"""Phase F70 — External editorial provider (Sportytrader + Forebet) tests.

These tests do NOT hit the network. We feed real HTML fixtures saved
from previous live runs (with scrape.do) into the parsers so the suite
remains deterministic and offline-safe.

Coverage (8 mandatory tests):
  1. scrape.do client: missing token → returns None (no exception).
  2. scrape.do client: circuit breaker opens after N failures.
  3. Sportytrader parser: extracts home/away/competition + recent
     results + team stats + final prediction.
  4. Sportytrader parser: invalid HTML → ``available=False`` (fail-soft).
  5. Forebet parser: parses fixtures index + handles unknown rows.
  6. Forebet parser: ``find_fixture`` works with accents and swapped
     orientation.
  7. external_editorial_provider: build_sportytrader_*_url helpers
     produce valid URLs.
  8. external_editorial_provider: fetch_external_editorial_for_match
     gracefully returns ``available=False`` when team names missing.
"""
from __future__ import annotations

import asyncio
import os

import pytest

from services import scrape_do_client
from services.forebet_scraper import (
    find_fixture,
    parse_forebet_fixtures_page,
)
from services.sportytrader_scraper import parse_sportytrader_match_page


# ─────────────────────────────────────────────────────────────────────
# T1 — scrape.do client: missing token returns None (fail-soft).
# ─────────────────────────────────────────────────────────────────────
def test_t1_scrapedo_no_token(monkeypatch):
    monkeypatch.delenv("SCRAPEDO_TOKEN", raising=False)
    monkeypatch.delenv("SCRAPE_DO_TOKEN", raising=False)
    monkeypatch.delenv("SCRAPEDO_API_KEY", raising=False)
    assert scrape_do_client.is_enabled() is False
    out = scrape_do_client.fetch_via_scrapedo_sync("https://example.com")
    assert out is None


# ─────────────────────────────────────────────────────────────────────
# T2 — scrape.do circuit breaker opens after N failures.
# ─────────────────────────────────────────────────────────────────────
def test_t2_scrapedo_breaker_opens(monkeypatch):
    monkeypatch.setenv("SCRAPEDO_TOKEN", "dummy")
    # Simulate failures: directly call the internal record helper.
    scrape_do_client._CB_FAILS.clear()
    scrape_do_client._CB_OPENED_AT.clear()
    for _ in range(scrape_do_client._CB_THRESHOLD):
        scrape_do_client._cb_record_failure("foo.test")
    assert "foo.test" in scrape_do_client._CB_OPENED_AT
    assert scrape_do_client._cb_open("foo.test") is True
    # Cool-down expired → breaker resets.
    scrape_do_client._CB_OPENED_AT["foo.test"] = 0.0
    assert scrape_do_client._cb_open("foo.test") is False
    scrape_do_client._CB_FAILS.clear()
    scrape_do_client._CB_OPENED_AT.clear()


# ─────────────────────────────────────────────────────────────────────
# T3 — Sportytrader parser on real HTML.
# ─────────────────────────────────────────────────────────────────────
SPORTY_HTML_PATH = "/tmp/sport_real.html"


@pytest.mark.skipif(not os.path.exists(SPORTY_HTML_PATH),
                     reason="sportytrader live fixture missing (re-fetch to run)")
def test_t3_sportytrader_parser_real():
    with open(SPORTY_HTML_PATH) as f:
        html = f.read()
    out = parse_sportytrader_match_page(html)
    assert out["available"] is True
    assert "Canadá" in out["home_team"] or "Canada" in out["home_team"]
    assert "Bosnia" in out["away_team"]
    assert out["competition"] == "Mundial"
    assert len(out["recent_results"]) >= 6
    # Each recent result has 4 keys at minimum.
    for r in out["recent_results"][:3]:
        assert "home_team" in r and "away_team" in r
        assert r["home_team"] != r["away_team"]
        assert r["home_score"] is not None
        assert r["away_score"] is not None
    # Team stats are exactly 2 (home + away).
    assert len(out["team_stats"]) == 2
    for ts in out["team_stats"]:
        assert ts["total_goals_avg"] is not None
        assert ts["btts_pct"] is not None
        assert ts["over_2_5_pct"] is not None
        assert "streak" in ts
    assert out["prediction"]["final_prediction"]


# ─────────────────────────────────────────────────────────────────────
# T4 — Sportytrader parser: invalid HTML is fail-soft.
# ─────────────────────────────────────────────────────────────────────
def test_t4_sportytrader_invalid_html_failsoft():
    assert parse_sportytrader_match_page("")["available"] is False
    assert parse_sportytrader_match_page("<html></html>")["available"] is False
    assert parse_sportytrader_match_page(None)["available"] is False  # type: ignore[arg-type]


# ─────────────────────────────────────────────────────────────────────
# T5 — Forebet parser on real HTML.
# ─────────────────────────────────────────────────────────────────────
FOREBET_HTML_PATH = "/tmp/forebet_real.html"


@pytest.mark.skipif(not os.path.exists(FOREBET_HTML_PATH),
                     reason="forebet live fixture missing (re-fetch to run)")
def test_t5_forebet_parser_real():
    with open(FOREBET_HTML_PATH) as f:
        html = f.read()
    out = parse_forebet_fixtures_page(html)
    assert out["available"] is True
    assert len(out["fixtures"]) >= 5
    # Every fixture carries 1X2 probabilities.
    for fx in out["fixtures"][:5]:
        assert fx["forebet_pct_1"] is not None
        assert fx["forebet_pct_x"] is not None
        assert fx["forebet_pct_2"] is not None
        assert fx["pick_1x2"] in ("1", "X", "2")
        assert "-" in fx["predicted_score"]


# ─────────────────────────────────────────────────────────────────────
# T6 — find_fixture: accents + swapped orientation + fuzzy.
# ─────────────────────────────────────────────────────────────────────
def test_t6_forebet_find_fixture():
    fake = {
        "available": True,
        "fixtures": [
            {"home_team": "Canada",
             "away_team": "Bosnia-Herzegovina",
             "forebet_pct_1": 41, "forebet_pct_x": 35, "forebet_pct_2": 24,
             "pick_1x2": "1", "predicted_score": "1-0", "goals_avg": 1.5},
            {"home_team": "Qatar",
             "away_team": "Switzerland",
             "forebet_pct_1": 19, "forebet_pct_x": 21, "forebet_pct_2": 60,
             "pick_1x2": "2", "predicted_score": "0-2", "goals_avg": 1.9},
        ],
    }
    # Accent-insensitive
    fx = find_fixture(fake, "Canadá", "Bosnia y Herzegovina")
    assert fx is not None and fx["pick_1x2"] == "1"
    # Plain
    fx = find_fixture(fake, "Qatar", "Switzerland")
    assert fx is not None and fx["pick_1x2"] == "2"
    # Swapped orientation
    fx = find_fixture(fake, "Switzerland", "Qatar")
    assert fx is not None and fx.get("_orientation") == "swapped"
    # Missing → None
    assert find_fixture(fake, "Brazil", "Morocco") is None


# ─────────────────────────────────────────────────────────────────────
# T7 — URL builders.
# ─────────────────────────────────────────────────────────────────────
def test_t7_url_builders():
    from services.external_editorial_provider import (
        build_sportytrader_match_url,
        build_sportytrader_search_url,
    )
    # Search URL must encode + use slug.
    search = build_sportytrader_search_url("Canadá", "Bosnia y Herzegovina")
    assert search is not None
    assert "canada" in search.lower()
    assert "bosnia" in search.lower()
    assert search.startswith("https://www.sportytrader.com/es/?s=")

    # Match URL with numeric id.
    deep = build_sportytrader_match_url("Canadá", "Bosnia y Herzegovina", 353713)
    assert deep is not None
    assert deep.endswith("353713/")
    assert "canada-bosnia-y-herzegovina" in deep

    # Missing args → None.
    assert build_sportytrader_search_url("", "") is None
    assert build_sportytrader_match_url("Foo", "Bar", "") is None


# ─────────────────────────────────────────────────────────────────────
# T8 — Orchestrator fail-soft when teams missing.
# ─────────────────────────────────────────────────────────────────────
def test_t8_orchestrator_failsoft_no_teams():
    from services.external_editorial_provider import (
        fetch_external_editorial_for_match,
    )
    out = asyncio.run(fetch_external_editorial_for_match({}))
    assert out["available"] is False
    assert "EXTERNAL_TEAMS_MISSING" in out["reason_codes"]
