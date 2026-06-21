"""Sprint-D9-OddsCascade · Tests para:
  * odds_portal_client (parser + cascade-aware fetcher)
  * odds_cascade (TheOddsAPI primario + OddsPortal fallback)
  * Sportytrader deprecation (short-circuit, sin HTTP)
"""
from __future__ import annotations

import pytest

from services.external_sources import odds_cascade as oc
from services.external_sources import odds_portal_client as opc


# ─────────────────────────────────────────────────────────────────────
# odds_portal_client — URL builder + parser
# ─────────────────────────────────────────────────────────────────────
def test_build_oddsportal_match_url_basic():
    url = opc.build_oddsportal_match_url("Real Madrid", "Barcelona",
                                         league_slug="laliga")
    assert url is not None
    assert "/soccer/laliga/" in url
    assert "real-madrid" in url
    assert "barcelona" in url


def test_build_oddsportal_match_url_search_fallback_without_league():
    url = opc.build_oddsportal_match_url("Arsenal", "Chelsea")
    assert url is not None
    assert "/search/results/" in url


def test_build_oddsportal_match_url_returns_none_on_empty_teams():
    assert opc.build_oddsportal_match_url("", "Chelsea") is None
    assert opc.build_oddsportal_match_url("Arsenal", "") is None


def test_parse_oddsportal_h2h_with_valid_triple():
    # HTML mínimo con anchor "average" + 3 odds plausibles.
    html = """
    <html><body>
    <div>Some preamble lorem ipsum dolor sit amet, consectetur adipiscing elit. </div>
    <div class="odds-block">Average</div>
    <div>2.10</div>
    <div>3.40</div>
    <div>3.20</div>
    </body></html>
    """ + ("x" * 250)  # garantiza len > 200
    out = opc.parse_oddsportal_h2h(html)
    assert out["available"] is True
    assert out["odd_home"] == 2.10
    assert out["odd_draw"] == 3.40
    assert out["odd_away"] == 3.20


def test_parse_oddsportal_h2h_rejects_implausible_triple():
    # Suma de implied probs muy fuera de rango (extraído de stats no de odds).
    html = """
    <html><body>
    <div>Average odds</div>
    <div>15.50</div>
    <div>1.05</div>
    <div>1.05</div>
    </body></html>
    """ + ("x" * 250)
    out = opc.parse_oddsportal_h2h(html)
    assert out["available"] is False
    # Implausible reason o no-triple (depende de qué match el regex primero)
    assert out["reason_code"] in (
        "ODDS_PORTAL_PARSE_IMPLAUSIBLE_TRIPLE",
        "ODDS_PORTAL_PARSE_NO_TRIPLE",
    )


def test_parse_oddsportal_h2h_returns_no_triple_when_empty_html():
    out = opc.parse_oddsportal_h2h("<html></html>")
    assert out["available"] is False
    assert out["reason_code"] == "ODDS_PORTAL_HTML_EMPTY"


# ─────────────────────────────────────────────────────────────────────
# odds_portal_client — fetcher fail-soft
# ─────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_fetch_oddsportal_h2h_returns_disabled_without_scrapedo_token(monkeypatch):
    monkeypatch.delenv("SCRAPEDO_TOKEN", raising=False)
    out = await opc.fetch_oddsportal_h2h("Arsenal", "Chelsea",
                                          league_slug="premier-league",
                                          use_cache=False)
    assert out["available"] is False
    assert out["reason_code"] == "ODDS_PORTAL_SCRAPEDO_DISABLED"
    assert "search_url" in out


@pytest.mark.asyncio
async def test_fetch_oddsportal_h2h_handles_fetch_failure(monkeypatch):
    monkeypatch.setenv("SCRAPEDO_TOKEN", "test-token-fake")

    async def _fake_fetch(url, timeout=45.0):
        return {"ok": False, "reason_code": "SCRAPEDO_TIMEOUT",
                "html": None, "status_code": None}

    monkeypatch.setattr(
        "services.scrape_do_client.fetch_via_scrapedo_result", _fake_fetch,
    )
    out = await opc.fetch_oddsportal_h2h("A", "B", league_slug="x",
                                          use_cache=False)
    assert out["available"] is False
    assert out["reason_code"] == "SCRAPEDO_TIMEOUT"


@pytest.mark.asyncio
async def test_fetch_oddsportal_h2h_success_path(monkeypatch):
    monkeypatch.setenv("SCRAPEDO_TOKEN", "test-token-fake")

    fake_html = """
    <html><body>
    <div>Bookmakers average</div>
    <div>2.10</div>
    <div>3.30</div>
    <div>3.40</div>
    </body></html>
    """ + ("x" * 250)

    async def _fake_fetch(url, timeout=45.0):
        return {"ok": True, "html": fake_html, "status_code": 200,
                "reason_code": "OK"}

    monkeypatch.setattr(
        "services.scrape_do_client.fetch_via_scrapedo_result", _fake_fetch,
    )

    out = await opc.fetch_oddsportal_h2h("Real Madrid", "Barcelona",
                                          league_slug="laliga",
                                          use_cache=False)
    assert out["available"] is True
    assert out["source"] == "oddsportal"
    assert out["odd_home"] == 2.10
    assert out["odd_draw"] == 3.30
    assert out["odd_away"] == 3.40


# ─────────────────────────────────────────────────────────────────────
# odds_cascade — orquestador
# ─────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_odds_cascade_uses_the_odds_api_when_available(monkeypatch):
    """Si TheOddsAPI devuelve el partido, OddsPortal NO se invoca."""

    async def _fake_the_odds_api(*, home, away, sport_key, regions, use_cache):
        return {
            "available":  True,
            "source":     "the_odds_api",
            "home_team":  home,
            "away_team":  away,
            "odd_home":   1.95,
            "odd_draw":   3.50,
            "odd_away":   4.10,
            "bookmaker":  "Bet365",
        }

    monkeypatch.setattr(oc, "_try_the_odds_api", _fake_the_odds_api)

    called_oddsportal = {"count": 0}

    async def _fake_oddsportal(*, home, away, league_slug, use_cache):
        called_oddsportal["count"] += 1
        return None

    monkeypatch.setattr(oc, "_try_odds_portal", _fake_oddsportal)

    out = await oc.fetch_direct_match_odds_cascade(
        "Arsenal", "Chelsea", sport_key="soccer_epl",
    )
    assert out["available"] is True
    assert out["source"] == "the_odds_api"
    assert called_oddsportal["count"] == 0
    assert out["cascade_audit"]["winner"] == "the_odds_api"


@pytest.mark.asyncio
async def test_odds_cascade_falls_back_to_oddsportal(monkeypatch):
    """Si TheOddsAPI no encuentra el partido, OddsPortal entra como fallback."""

    async def _fake_the_odds_api(*, home, away, sport_key, regions, use_cache):
        return None  # No found

    async def _fake_oddsportal(*, home, away, league_slug, use_cache):
        return {
            "available": True,
            "source":    "oddsportal",
            "odd_home":  2.30,
            "odd_draw":  3.25,
            "odd_away":  3.10,
            "bookmaker": "oddsportal_avg",
        }

    monkeypatch.setattr(oc, "_try_the_odds_api", _fake_the_odds_api)
    monkeypatch.setattr(oc, "_try_odds_portal", _fake_oddsportal)
    monkeypatch.setenv("ENABLE_ODDS_CASCADE_FALLBACK", "true")

    out = await oc.fetch_direct_match_odds_cascade(
        "Liga MX Team A", "Liga MX Team B", sport_key="soccer_mexico_ligamx",
        league_slug="liga-mx",
    )
    assert out["available"] is True
    assert out["source"] == "oddsportal"
    assert out["cascade_audit"]["winner"] == "oddsportal"
    assert "the_odds_api" in out["cascade_audit"]["sources_tried"]
    assert "oddsportal"   in out["cascade_audit"]["sources_tried"]


@pytest.mark.asyncio
async def test_odds_cascade_returns_unavailable_when_both_fail(monkeypatch):
    async def _none(*args, **kwargs):
        return None

    monkeypatch.setattr(oc, "_try_the_odds_api", _none)
    monkeypatch.setattr(oc, "_try_odds_portal", _none)
    monkeypatch.setenv("ENABLE_ODDS_CASCADE_FALLBACK", "true")

    out = await oc.fetch_direct_match_odds_cascade("X", "Y")
    assert out["available"] is False
    assert out["source"] == "none"
    assert out["cascade_audit"]["winner"] is None
    assert "THE_ODDS_API_NO_MATCH" in out["cascade_audit"]["reason_codes"]
    assert "ODDS_PORTAL_NO_MATCH"  in out["cascade_audit"]["reason_codes"]


@pytest.mark.asyncio
async def test_odds_cascade_respects_fallback_disabled_flag(monkeypatch):
    """Si ENABLE_ODDS_CASCADE_FALLBACK=false, OddsPortal NO se invoca
    tras un miss de TheOddsAPI."""

    async def _none_toa(*, home, away, sport_key, regions, use_cache):
        return None

    called = {"count": 0}

    async def _spy_oddsportal(*, home, away, league_slug, use_cache):
        called["count"] += 1
        return {"available": True, "source": "oddsportal", "odd_home": 2.0,
                "odd_draw": 3.0, "odd_away": 3.5}

    monkeypatch.setattr(oc, "_try_the_odds_api", _none_toa)
    monkeypatch.setattr(oc, "_try_odds_portal", _spy_oddsportal)
    monkeypatch.setenv("ENABLE_ODDS_CASCADE_FALLBACK", "false")

    out = await oc.fetch_direct_match_odds_cascade("A", "B")
    assert out["available"] is False
    assert called["count"] == 0
    assert "ODDS_CASCADE_FALLBACK_DISABLED" in out["cascade_audit"]["reason_codes"]


@pytest.mark.asyncio
async def test_odds_cascade_rejects_empty_teams():
    out = await oc.fetch_direct_match_odds_cascade("", "")
    assert out["available"] is False
    assert "ODDS_CASCADE_TEAMS_MISSING" in out["cascade_audit"]["reason_codes"]


# ─────────────────────────────────────────────────────────────────────
# Sportytrader deprecation — short-circuit, sin HTTP
# ─────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_fetch_sportytrader_match_is_deprecated_no_http(monkeypatch):
    """``fetch_sportytrader_match`` debe retornar inmediatamente sin
    invocar scrape.do / Bright Data ni el parser."""

    from services import external_editorial_provider as eep

    # Si alguien llamara a scrape.do o al parser, fallaríamos el test.
    def _should_not_be_called(*args, **kwargs):
        raise AssertionError("SportyTrader scraper NO debe invocarse "
                              "tras la deprecación.")

    monkeypatch.setattr("services.scrape_do_client.fetch_via_scrapedo",
                         _should_not_be_called, raising=False)
    monkeypatch.setattr(
        "services.sportytrader_scraper.parse_sportytrader_match_page",
        _should_not_be_called, raising=False,
    )

    out = await eep.fetch_sportytrader_match(
        "https://www.sportytrader.com/es/pronosticos/foo-bar-1234/",
    )
    assert out["available"] is False
    assert out["deprecated"] is True
    assert out["replaced_by"] == "odds_cascade"
    assert "SPORTYTRADER_DEPRECATED" in out["reason_codes"]
