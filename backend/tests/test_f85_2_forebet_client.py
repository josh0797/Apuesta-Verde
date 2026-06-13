"""Phase F85.2 — Tests for the Forebet match-detail client."""
from __future__ import annotations

import httpx
import pytest

from services.external_sources import forebet_client as fcli


_GOOD_HTML = """
<html>
<head>
<meta property="og:title" content="USA vs Paraguay, Forebet Predictions" />
</head>
<body>
<h1>USA vs Paraguay</h1>
<div class="prediction-score">Predicción: 2-1</div>
<div class="pick">1</div>
<div class="prob">
  <span class="percent home">45%</span>
  <span class="percent draw">30%</span>
  <span class="percent away">25%</span>
</div>
<div class="analysis">USA llega con buen ritmo ofensivo. Promedio goles: 2.4
  esperados. Probable Over 2.5.</div>
</body></html>
"""


_NO_PAYLOAD_HTML = """
<html><body><div>Landing page with no match data.</div></body></html>
"""


# =====================================================================
# Parsing
# =====================================================================
class TestParseForebetMatchHtml:
    def test_parses_full_payload(self):
        out = fcli.parse_forebet_match_html(_GOOD_HTML, source_url="https://www.forebet.com/es/x")
        assert out["available"] is True
        assert out["source"]    == "forebet"
        assert out["match_url"] == "https://www.forebet.com/es/x"
        assert out["home_team"].lower().startswith("usa")
        assert out["away_team"].lower().startswith("paraguay")
        assert out["predicted_score"] == "2-1"
        assert out["prediction"] == "1"
        assert out["probabilities"] == {"home": 45, "draw": 30, "away": 25}
        assert out["goals_context"]["avg_goals_hint"] == 2.4
        assert out["goals_context"]["over_2_5_hint"] is True
        assert fcli.RC_CONTEXT_AVAILABLE in out["reason_codes"]

    def test_empty_html_returns_unavailable(self):
        out = fcli.parse_forebet_match_html("")
        assert out["available"] is False
        assert fcli.RC_UNAVAILABLE in out["reason_codes"]

    def test_landing_with_no_payload_returns_parse_failed(self):
        out = fcli.parse_forebet_match_html(_NO_PAYLOAD_HTML)
        assert out["available"] is False
        assert fcli.RC_PARSE_FAILED in out["reason_codes"]

    @pytest.mark.parametrize("bad", [None, 42, [], {}])
    def test_invalid_inputs_return_unavailable(self, bad):
        out = fcli.parse_forebet_match_html(bad)  # type: ignore[arg-type]
        assert out["available"] is False

    def test_score_only_still_yields_payload(self):
        html = '<html><body><div class="predicted">1-1</div></body></html>'
        out = fcli.parse_forebet_match_html(html)
        assert out["available"] is True
        assert out["predicted_score"] == "1-1"

    def test_comma_decimal_avg_goals_is_handled(self):
        html = ('<html><body><div class="analysis">Promedio goles: 2,8 esperados</div>'
                '<div class="predicted">2-1</div></body></html>')
        out = fcli.parse_forebet_match_html(html)
        assert out["goals_context"]["avg_goals_hint"] == 2.8

    def test_under_3_5_hint_extracted(self):
        html = ('<html><body><div class="predicted">1-1</div>'
                '<div class="analysis">Recomendable Under 3.5</div></body></html>')
        out = fcli.parse_forebet_match_html(html)
        assert out["goals_context"]["under_3_5_hint"] is True


# =====================================================================
# Network layer
# =====================================================================
class TestFetchForebetMatchContext:
    @pytest.mark.asyncio
    async def test_missing_url_returns_unavailable(self):
        out = await fcli.fetch_forebet_match_context(None, "")
        assert out["available"] is False
        assert fcli.RC_URL_MISSING in out["reason_codes"]

    @pytest.mark.asyncio
    async def test_non_forebet_url_rejected(self):
        out = await fcli.fetch_forebet_match_context(
            None, "https://evil.example/forebet",
        )
        assert out["available"] is False
        assert fcli.RC_URL_MISSING in out["reason_codes"]

    @pytest.mark.asyncio
    async def test_404_returns_unavailable(self):
        async with httpx.AsyncClient(transport=httpx.MockTransport(
            lambda r: httpx.Response(404, text="not found"),
        )) as c:
            out = await fcli.fetch_forebet_match_context(
                c, "https://www.forebet.com/es/football/matches/usa-paraguay-1",
            )
        assert out["available"] is False
        assert fcli.RC_UNAVAILABLE in out["reason_codes"]

    @pytest.mark.asyncio
    async def test_happy_path_full_round_trip(self):
        async with httpx.AsyncClient(transport=httpx.MockTransport(
            lambda r: httpx.Response(200, text=_GOOD_HTML),
        )) as c:
            out = await fcli.fetch_forebet_match_context(
                c, "https://www.forebet.com/es/football/matches/usa-paraguay-1",
            )
        assert out["available"] is True
        assert out["predicted_score"] == "2-1"
        assert out["probabilities"]["home"] == 45
