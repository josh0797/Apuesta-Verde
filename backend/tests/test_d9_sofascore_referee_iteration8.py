"""Sprint-D9-HOTFIX4 · Tests del extractor de árbitros Sofascore
(HTML público + Scrape.do).

Validaciones:
  1. build_match_url — slugs con / sin code, con / sin lang.
  2. parse_sofascore_match_next_data — happy path con __NEXT_DATA__
     real (datos del partido Iran vs Belgium del usuario).
  3. parse — referee no asignado.
  4. parse — fail-soft cuando HTML está vacío o sin __NEXT_DATA__.
  5. fetch_sofascore_referee_for_match — skip cuando SCRAPEDO_TOKEN
     no está configurado.
  6. fetch — happy path con scrape.do mockeado.
  7. fetch — fail-soft cuando scrape.do devuelve non-ok.
"""
from __future__ import annotations

import json

import pytest

from services.external_sources import sofascore_referee as sr


# ─────────────────────────────────────────────────────────────────────
# build_match_url
# ─────────────────────────────────────────────────────────────────────
def test_build_match_url_with_code_and_lang_es():
    url = sr.build_match_url("Iran", "Belgium", code="rUbsqVb", lang="es")
    assert url == "https://www.sofascore.com/es/football/match/iran-belgium/rUbsqVb"


def test_build_match_url_without_code():
    url = sr.build_match_url("Real Madrid", "Barcelona")
    assert url == "https://www.sofascore.com/es/football/match/real-madrid-barcelona"


def test_build_match_url_with_special_chars():
    url = sr.build_match_url("Curaçao", "São Paulo", code="abc")
    assert url is not None
    assert "curacao" in url
    assert "sao-paulo" in url


def test_build_match_url_empty_teams():
    assert sr.build_match_url("", "Belgium") is None
    assert sr.build_match_url("Iran", "") is None


# ─────────────────────────────────────────────────────────────────────
# parse_sofascore_match_next_data
# ─────────────────────────────────────────────────────────────────────
_REAL_PAYLOAD_TEMPLATE = {
    "props": {
        "pageProps": {
            "event": {
                "id": 15186499,
                "slug": "iran-belgium",
                "homeTeam": {"name": "Belgium"},
                "awayTeam": {"name": "Iran"},
                "startTimestamp": 1782255600,
                "season": {"name": "World Cup 2026"},
                "tournament": {"name": "FIFA World Cup, Group G"},
                "venue": {
                    "stadium": {"name": "SoFi Stadium"},
                    "city": {"name": "Inglewood"},
                    "country": {"name": "USA"},
                },
                "referee": {
                    "id": 322839,
                    "slug": "dario-herrera",
                    "name": "Dario Herrera",
                    "country": {
                        "alpha2": "AR",
                        "alpha3": "ARG",
                        "name": "Argentina",
                        "slug": "argentina",
                    },
                    "yellowCards": 2534,
                    "redCards": 99,
                    "yellowRedCards": 79,
                    "games": 466,
                    "sport": {"id": 1, "slug": "football", "name": "Football"},
                },
            }
        }
    }
}


def _wrap_html(next_data_obj) -> str:
    js = json.dumps(next_data_obj)
    return f"""
    <html><body>
    {'x' * 600}
    <script id="__NEXT_DATA__" type="application/json">{js}</script>
    </body></html>
    """


def test_parse_happy_path_extracts_referee_full_data():
    out = sr.parse_sofascore_match_next_data(
        _wrap_html(_REAL_PAYLOAD_TEMPLATE)
    )
    assert out["available"] is True
    assert out["source"] == "sofascore"
    assert out["fetch_method"] == "scrapedo+html"
    assert out["match_id"] == 15186499
    assert out["match_slug"] == "iran-belgium"
    assert out["competition"] == "FIFA World Cup, Group G"
    assert out["stadium"] == "SoFi Stadium"
    assert out["city"] == "Inglewood, USA"

    ref = out["referee"]
    assert ref["id"] == 322839
    assert ref["slug"] == "dario-herrera"
    assert ref["name"] == "Dario Herrera"
    assert ref["country"]["alpha2"] == "AR"
    assert ref["country"]["name"] == "Argentina"
    assert ref["games"] == 466
    assert ref["yellow_cards"] == 2534
    assert ref["red_cards"] == 99
    assert ref["yellow_red_cards"] == 79
    # Promedios: comparamos con tolerancia
    assert abs(ref["yellow_cards_per_game"] - 5.438) < 0.01  # captura UI 5.44
    assert abs(ref["all_red_cards_per_game"] - 0.382) < 0.01  # captura UI 0.38
    assert abs(ref["red_cards_per_game"] - 0.212) < 0.01
    assert abs(ref["second_yellow_per_game"] - 0.17) < 0.01

    assert ref["profile_url"] == (
        "https://www.sofascore.com/es/football/referee/dario-herrera/322839"
    )


def test_parse_referee_not_assigned():
    payload = json.loads(json.dumps(_REAL_PAYLOAD_TEMPLATE))
    payload["props"]["pageProps"]["event"]["referee"] = None
    out = sr.parse_sofascore_match_next_data(_wrap_html(payload))
    assert out["available"] is False
    assert "REFEREE_NOT_ASSIGNED_BY_SOFASCORE" in out["reason_codes"]
    # Aún devuelve match_label porque el event sí está poblado.
    assert out["match_label"] == "Belgium vs Iran"


def test_parse_fail_soft_when_html_empty():
    out = sr.parse_sofascore_match_next_data("")
    assert out["available"] is False
    assert "REFEREE_HTML_EMPTY_OR_SHORT" in out["reason_codes"]


def test_parse_fail_soft_when_next_data_missing():
    out = sr.parse_sofascore_match_next_data(
        "<html><body>" + ("x" * 1000) + "</body></html>"
    )
    assert out["available"] is False
    assert "REFEREE_NEXT_DATA_NOT_FOUND" in out["reason_codes"]


def test_parse_fail_soft_when_next_data_malformed():
    html = (
        "<html><body>" + ("x" * 600) + ''
        '<script id="__NEXT_DATA__" type="application/json">{not valid json</script>'
        "</body></html>"
    )
    out = sr.parse_sofascore_match_next_data(html)
    assert out["available"] is False
    assert any("REFEREE_NEXT_DATA_PARSE_FAILED" in c for c in out["reason_codes"])


# ─────────────────────────────────────────────────────────────────────
# fetch_sofascore_referee_for_match (orquestador con Scrape.do mock)
# ─────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_fetch_skips_when_scrapedo_token_missing(monkeypatch):
    monkeypatch.delenv("SCRAPEDO_TOKEN", raising=False)
    out = await sr.fetch_sofascore_referee_for_match(
        "Iran", "Belgium", code="rUbsqVb", use_cache=False,
    )
    assert out["available"] is False
    assert "REFEREE_SCRAPEDO_DISABLED" in out["reason_codes"]
    assert "source_url" in out


@pytest.mark.asyncio
async def test_fetch_happy_path_with_mocked_scrapedo(monkeypatch):
    monkeypatch.setenv("SCRAPEDO_TOKEN", "test-token-fake")
    fake_html = _wrap_html(_REAL_PAYLOAD_TEMPLATE)

    async def _fake_fetch(url, timeout=45.0, render=True):
        return {"ok": True, "status_code": 200, "html": fake_html,
                "reason_code": None}

    monkeypatch.setattr(
        "services.scrape_do_client.fetch_via_scrapedo_result", _fake_fetch,
    )

    out = await sr.fetch_sofascore_referee_for_match(
        "Iran", "Belgium", code="rUbsqVb", use_cache=False,
    )
    assert out["available"] is True
    assert out["referee"]["name"] == "Dario Herrera"
    assert abs(out["referee"]["all_red_cards_per_game"] - 0.382) < 0.01


@pytest.mark.asyncio
async def test_fetch_fail_soft_when_scrapedo_returns_non_ok(monkeypatch):
    monkeypatch.setenv("SCRAPEDO_TOKEN", "test-token-fake")

    async def _fake_fetch_fail(url, timeout=45.0, render=True):
        return {"ok": False, "status_code": 403, "html": None,
                "reason_code": "SCRAPEDO_BLOCKED"}

    monkeypatch.setattr(
        "services.scrape_do_client.fetch_via_scrapedo_result", _fake_fetch_fail,
    )

    out = await sr.fetch_sofascore_referee_for_match(
        "Iran", "Belgium", code="rUbsqVb", use_cache=False,
    )
    assert out["available"] is False
    assert "SCRAPEDO_BLOCKED" in out["reason_codes"]
    assert out["status_code"] == 403


@pytest.mark.asyncio
async def test_fetch_returns_teams_missing_with_empty_inputs():
    out = await sr.fetch_sofascore_referee_for_match("", "Belgium")
    assert out["available"] is False
    assert "REFEREE_TEAMS_MISSING" in out["reason_codes"]
