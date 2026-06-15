"""Phase F93 — TotalCorner + FootyStats clients + corners cascade tests.

Validates:
  * URL resolvers for ``external_ids.totalcorner`` and
    ``external_ids.footystats`` (and legacy top-level fields).
  * HTML parsers (corner detection across the realistic shapes seen in
    each site's match pages).
  * Fail-soft contract — failures NEVER raise; they always return a
    structured ``reason_code``.
  * Cascade order is **TheStatsAPI → API-Sports → TotalCorner → 365Scores
    → FootyStats** when ``ENABLE_F93_CASCADE_ORDER=true`` (the default).
  * Cascade still respects the legacy F83 flag for backwards compat.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from services import football_corners_provider as fcp
from services.external_sources import (
    footystats_scrapedo_client as fs,
    totalcorner_scrapedo_client as tc,
)


# =====================================================================
# TotalCorner — URL resolver
# =====================================================================
class TestTotalCornerResolver:
    def test_explicit_match_url_wins(self):
        match_doc = {"external_ids": {"totalcorner": {
            "match_url": "https://www.totalcorner.com/matches/view/9999",
            "match_id":  9999,
        }}}
        out = tc.extract_totalcorner_match_url(match_doc)
        assert out["available"] is True
        assert out["source"] == "explicit"
        assert out["match_url"].endswith("/matches/view/9999")
        assert out["match_id"] == "9999"

    def test_match_id_only_builds_canonical_url(self):
        match_doc = {"external_ids": {"totalcorner": {"match_id": 42}}}
        out = tc.extract_totalcorner_match_url(match_doc)
        assert out["available"] is True
        assert out["source"] == "match_id"
        assert out["match_url"] == "https://www.totalcorner.com/matches/42"

    def test_top_level_legacy_field_supported(self):
        match_doc = {"totalcorner_match_id": "777"}
        out = tc.extract_totalcorner_match_url(match_doc)
        assert out["available"] is True
        assert out["match_url"].endswith("/matches/777")

    def test_missing_returns_unavailable(self):
        for bad in [{}, {"external_ids": {}}, None, "not-a-dict", 42]:
            out = tc.extract_totalcorner_match_url(bad)
            assert out["available"] is False


# =====================================================================
# TotalCorner — HTML parser
# =====================================================================
class TestTotalCornerParser:
    def test_empty_html_returns_stats_empty(self):
        for bad in ["", None, "   "]:
            out = tc.parse_totalcorner_corners_from_html(bad)
            assert out["available"] is False
            assert out["reason_code"] == tc.RC_STATS_EMPTY

    def test_parses_canonical_stat_row(self):
        html = """
        <html><body><table>
          <tr><th>Shots</th><td>12</td><td>9</td></tr>
          <tr><th>Corners</th><td>9</td><td>5</td></tr>
          <tr><th>Fouls</th><td>11</td><td>14</td></tr>
        </table></body></html>
        """
        out = tc.parse_totalcorner_corners_from_html(html)
        assert out["available"] is True
        assert out["home"]["corners"] == 9
        assert out["away"]["corners"] == 5
        assert out["total_corners"] == 14
        assert out["reason_code"] == tc.RC_CORNERS_FOUND
        assert out["confidence"] == "USABLE"

    def test_handles_corner_kicks_alias(self):
        html = "<tr><td>Corner Kicks</td><td>7</td><td>3</td></tr>"
        out = tc.parse_totalcorner_corners_from_html(html)
        assert out["available"] is True
        assert out["home"]["corners"] == 7
        assert out["away"]["corners"] == 3
        assert out["total_corners"] == 10

    def test_handles_spanish_alias_with_accent(self):
        html = "<tr><th>Tiros de esquina</th><td>4</td><td>6</td></tr>"
        out = tc.parse_totalcorner_corners_from_html(html)
        assert out["available"] is True
        assert out["home"]["corners"] == 4
        assert out["away"]["corners"] == 6

    def test_stats_present_but_no_corners(self):
        html = """
        <tr><th>Shots</th><td>12</td><td>9</td></tr>
        <tr><th>Fouls</th><td>11</td><td>14</td></tr>
        """
        out = tc.parse_totalcorner_corners_from_html(html)
        assert out["available"] is False
        assert out["reason_code"] == tc.RC_CORNERS_NOT_FOUND


# =====================================================================
# TotalCorner — Fetch fail-soft
# =====================================================================
class TestTotalCornerFetch:
    @pytest.mark.asyncio
    async def test_empty_url_returns_url_missing(self):
        out = await tc.fetch_totalcorner_match_page(None, "")
        assert out["available"] is False
        assert out["reason_code"] == tc.RC_URL_MISSING
        assert out["retryable"] is False

    @pytest.mark.asyncio
    async def test_scrapedo_ok_returns_html(self, monkeypatch):
        async def fake_fetch(*a, **kw):
            return {"ok": True, "html": "<tr><th>Corners</th><td>4</td><td>3</td></tr>",
                    "status_code": 200, "reason_code": None, "message_debug": None}
        monkeypatch.setattr("services.external_sources.totalcorner_scrapedo_client.fetch_via_scrapedo_result",
                            fake_fetch)
        out = await tc.fetch_totalcorner_match_page(None, "https://www.totalcorner.com/matches/1")
        assert out["available"] is True
        assert "<tr><th>Corners" in out["html"]

    @pytest.mark.asyncio
    async def test_scrapedo_http_403_maps_to_blocked(self, monkeypatch):
        async def fake_fetch(*a, **kw):
            return {"ok": False, "html": None, "status_code": 403,
                    "reason_code": "SCRAPEDO_HTTP_ERROR",
                    "message_debug": "blocked"}
        monkeypatch.setattr("services.external_sources.totalcorner_scrapedo_client.fetch_via_scrapedo_result",
                            fake_fetch)
        out = await tc.fetch_totalcorner_match_page(None, "https://www.totalcorner.com/matches/1")
        assert out["available"] is False
        assert out["reason_code"] == tc.RC_BLOCKED_OR_FORBIDDEN


# =====================================================================
# FootyStats — URL resolver
# =====================================================================
class TestFootyStatsResolver:
    def test_explicit_url(self):
        m = {"external_ids": {"footystats": {
            "match_url": "https://footystats.org/uk/team-x-vs-team-y",
        }}}
        out = fs.extract_footystats_match_url(m)
        assert out["available"] is True
        assert out["source"] == "explicit"

    def test_slug_builds_canonical_url(self):
        m = {"external_ids": {"footystats": {"slug": "england/match/team-a-vs-team-b-h2h"}}}
        out = fs.extract_footystats_match_url(m)
        assert out["available"] is True
        assert out["match_url"].startswith("https://footystats.org/")
        assert out["match_url"].endswith("team-a-vs-team-b-h2h")
        assert out["source"] == "slug"

    def test_missing(self):
        for bad in [{}, {"external_ids": {}}, None]:
            out = fs.extract_footystats_match_url(bad)
            assert out["available"] is False


# =====================================================================
# FootyStats — HTML parser
# =====================================================================
class TestFootyStatsParser:
    def test_empty(self):
        for bad in ["", None]:
            out = fs.parse_footystats_corners_from_html(bad)
            assert out["available"] is False
            assert out["reason_code"] == fs.RC_STATS_EMPTY

    def test_data_stat_pattern(self):
        html = """
        <div data-stat="corners" class="card">
          <span class="home">8</span>
          <span class="away">4</span>
        </div>
        """
        out = fs.parse_footystats_corners_from_html(html)
        assert out["available"] is True
        assert out["home"]["corners"] == 8
        assert out["away"]["corners"] == 4
        assert out["total_corners"] == 12

    def test_label_then_numbers(self):
        # Loose: label followed by two numbers within the next 200 chars.
        html = """
        <section><h3>Match Stats</h3>
        <p>Corners ... home <strong>6</strong> &mdash; away <strong>2</strong></p>
        </section>
        """
        out = fs.parse_footystats_corners_from_html(html)
        assert out["available"] is True
        assert out["home"]["corners"] == 6
        assert out["away"]["corners"] == 2
        assert out["confidence"] == "LIMITED"

    def test_no_corners_block(self):
        html = "<p>Shots 12 to 9</p>"
        out = fs.parse_footystats_corners_from_html(html)
        assert out["available"] is False
        assert out["reason_code"] in (fs.RC_CORNERS_NOT_FOUND,)


# =====================================================================
# FootyStats — Fetch fail-soft
# =====================================================================
class TestFootyStatsFetch:
    @pytest.mark.asyncio
    async def test_empty_url(self):
        out = await fs.fetch_footystats_match_page(None, "")
        assert out["available"] is False
        assert out["reason_code"] == fs.RC_URL_MISSING

    @pytest.mark.asyncio
    async def test_scrapedo_timeout(self, monkeypatch):
        async def fake_fetch(*a, **kw):
            return {"ok": False, "html": None, "status_code": None,
                    "reason_code": "SCRAPEDO_TIMEOUT", "message_debug": "timeout"}
        monkeypatch.setattr("services.external_sources.footystats_scrapedo_client.fetch_via_scrapedo_result",
                            fake_fetch)
        out = await fs.fetch_footystats_match_page(None, "https://footystats.org/match/x")
        assert out["available"] is False
        assert out["reason_code"] == "SCRAPEDO_TIMEOUT"
        assert out["retryable"] is True


# =====================================================================
# Cascade order — F93 spec
# =====================================================================
class TestF93CascadeOrder:
    def test_default_is_f93(self, monkeypatch):
        # Clear competing flags.
        monkeypatch.delenv("ENABLE_F93_CASCADE_ORDER", raising=False)
        monkeypatch.delenv("ENABLE_F83_CASCADE_ORDER", raising=False)
        order, flag = fcp._resolve_cascade_order()
        assert flag == "F93"
        assert order == ["thestatsapi", "api_sports", "totalcorner",
                          "365scores", "footystats"]

    def test_explicit_f93_true(self, monkeypatch):
        monkeypatch.setenv("ENABLE_F93_CASCADE_ORDER", "true")
        order, flag = fcp._resolve_cascade_order()
        assert flag == "F93"
        assert order[2] == "totalcorner"
        assert order[3] == "365scores"
        assert order[4] == "footystats"

    def test_opt_out_f93_falls_to_f82_2(self, monkeypatch):
        monkeypatch.setenv("ENABLE_F93_CASCADE_ORDER", "false")
        monkeypatch.setenv("ENABLE_F83_CASCADE_ORDER", "false")
        order, flag = fcp._resolve_cascade_order()
        assert flag == "F82.2"
        assert order == ["thestatsapi", "api_sports", "365scores"]

    def test_legacy_f83_still_honoured_when_f93_disabled(self, monkeypatch):
        monkeypatch.setenv("ENABLE_F93_CASCADE_ORDER", "false")
        monkeypatch.setenv("ENABLE_F83_CASCADE_ORDER", "true")
        order, flag = fcp._resolve_cascade_order()
        assert flag == "F83"
        assert order == ["api_sports", "365scores", "thestatsapi"]


# =====================================================================
# debug_corners_cascade — end-to-end (mock external probes)
# =====================================================================
class TestDebugCornersCascade:
    @pytest.mark.asyncio
    async def test_thestatsapi_wins_no_external_calls(self, monkeypatch):
        monkeypatch.setenv("ENABLE_F93_CASCADE_ORDER", "true")
        match_doc = {
            "match_id":   "tsa-wins",
            "home_team":  {"name": "Home"},
            "away_team":  {"name": "Away"},
            "_thestatsapi_enrichment": {"corners": {"home": 7, "away": 3, "total": 10}},
        }
        # External probes must NOT be called when TSA wins early.
        tc_spy = AsyncMock()
        fs_spy = AsyncMock()
        with patch.object(fcp, "_f93_check_totalcorner", tc_spy), \
             patch.object(fcp, "_f93_check_footystats", fs_spy):
            out = await fcp.debug_corners_cascade(match_doc, allow_external=True)
        assert out["final"]["available"] is True
        assert out["winner"]["provider"] == "thestatsapi"
        assert out["cascade_flag"] == "F93"
        tc_spy.assert_not_awaited()
        fs_spy.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_totalcorner_used_when_tsa_and_aps_empty(self, monkeypatch):
        monkeypatch.setenv("ENABLE_F93_CASCADE_ORDER", "true")
        match_doc = {"match_id": "tc-wins",
                      "home_team": {"name": "H"}, "away_team": {"name": "A"}}

        async def fake_tc(md, *, timeout_s):
            return {
                "provider": "totalcorner", "transport": "scrape_do",
                "available": True, "stage": "PARSE_HTML",
                "data": {"home": 9, "away": 5, "total": 14},
                "reason_code": tc.RC_CORNERS_FOUND, "confidence": "USABLE",
            }
        async def fake_365(md, *, timeout_s):
            raise AssertionError("365scores must not be called when TC wins")
        async def fake_fs(md, *, timeout_s):
            raise AssertionError("footystats must not be called when TC wins")

        with patch.object(fcp, "_f93_check_totalcorner", fake_tc), \
             patch.object(fcp, "_f83_check_365scores", fake_365), \
             patch.object(fcp, "_f93_check_footystats", fake_fs):
            out = await fcp.debug_corners_cascade(match_doc, allow_external=True)

        assert out["final"]["available"] is True
        assert out["winner"]["provider"] == "totalcorner"
        # Cascade reflects probe order: tsa, api_sports, totalcorner = 3 entries.
        provs = [e["provider"] for e in out["providers_checked"]]
        assert provs[:3] == ["thestatsapi", "api_sports", "totalcorner"]
        assert "365scores" not in provs
        assert "footystats" not in provs

    @pytest.mark.asyncio
    async def test_footystats_is_last_resort(self, monkeypatch):
        monkeypatch.setenv("ENABLE_F93_CASCADE_ORDER", "true")
        match_doc = {"match_id": "fs-wins",
                      "home_team": {"name": "H"}, "away_team": {"name": "A"}}

        async def fake_tc(md, *, timeout_s):
            return {"provider": "totalcorner", "available": False,
                    "stage": "URL_RESOLUTION", "reason_code": tc.RC_URL_MISSING}
        async def fake_365(md, *, timeout_s):
            return {"provider": "365scores", "available": False,
                    "stage": "ID_RESOLUTION", "reason_code": "SCORE365_ID_MISSING"}
        async def fake_fs(md, *, timeout_s):
            return {
                "provider": "footystats", "transport": "scrape_do",
                "available": True, "stage": "PARSE_HTML",
                "data": {"home": 5, "away": 4, "total": 9},
                "reason_code": fs.RC_CORNERS_FOUND, "confidence": "USABLE",
            }

        with patch.object(fcp, "_f93_check_totalcorner", fake_tc), \
             patch.object(fcp, "_f83_check_365scores", fake_365), \
             patch.object(fcp, "_f93_check_footystats", fake_fs):
            out = await fcp.debug_corners_cascade(match_doc, allow_external=True)

        assert out["final"]["available"] is True
        assert out["winner"]["provider"] == "footystats"
        provs = [e["provider"] for e in out["providers_checked"]]
        assert provs == ["thestatsapi", "api_sports", "totalcorner",
                          "365scores", "footystats"]

    @pytest.mark.asyncio
    async def test_no_provider_skips_external_when_allow_external_false(self, monkeypatch):
        monkeypatch.setenv("ENABLE_F93_CASCADE_ORDER", "true")
        match_doc = {"match_id": "skipped",
                      "home_team": {"name": "H"}, "away_team": {"name": "A"}}
        # Spies just to assert NOT awaited.
        tc_spy = AsyncMock()
        fs_spy = AsyncMock()
        ts_spy = AsyncMock()
        with patch.object(fcp, "_f93_check_totalcorner", tc_spy), \
             patch.object(fcp, "_f93_check_footystats", fs_spy), \
             patch.object(fcp, "_f83_check_365scores", ts_spy):
            out = await fcp.debug_corners_cascade(match_doc, allow_external=False)

        # All HTTP probes must be skipped, but their entries still appear
        # in providers_checked with reason_code = *_SKIPPED_INLINE.
        rcs = [e["reason_code"] for e in out["providers_checked"]]
        assert any("SKIPPED" in (rc or "") for rc in rcs)
        tc_spy.assert_not_awaited()
        fs_spy.assert_not_awaited()
        ts_spy.assert_not_awaited()
        assert out["final"]["available"] is False
        assert out["final"]["reason_code"] == fcp.RC_NO_PROVIDER_AVAILABLE


# =====================================================================
# Non-raising contract
# =====================================================================
class TestFailSoft:
    @pytest.mark.asyncio
    async def test_debug_with_non_dict_doc_returns_dict(self):
        out = await fcp.debug_corners_cascade(None)
        assert isinstance(out, dict)
        assert out["final"]["available"] is False

    def test_resolvers_never_raise(self):
        for bad in [None, 0, "", [], {}]:
            assert tc.extract_totalcorner_match_url(bad)["available"] is False
            assert fs.extract_footystats_match_url(bad)["available"] is False

    def test_parsers_never_raise(self):
        for bad in [None, 0, "", "<broken html ", "<tr></tr>"]:
            tc.parse_totalcorner_corners_from_html(bad)
            fs.parse_footystats_corners_from_html(bad)


# pytest-asyncio loop policy fallback (in case the conftest doesn't enforce mode).
if not hasattr(pytest, "asyncio_loop_policy"):  # pragma: no cover
    asyncio.set_event_loop_policy(asyncio.DefaultEventLoopPolicy())
