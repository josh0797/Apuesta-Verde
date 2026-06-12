"""Phase F58+ — Scores24 scraper smoke tests.

Cubre:
  * Parsing de la página real (HTML fixture con las 3 secciones).
  * Extracción explícita del bet ("under 9.5 córners", "over 1.5 goles").
  * Cuota detectada en el texto ("Cuota: 1.58*").
  * Reason codes esperados (SCORES24_CORNERS_PREDICTION_FOUND, etc.).
  * Fail-soft cuando Bright Data devuelve None / vacío / URL inválida.
  * Consensus (primary_market picks Apuesta Fiable > Corners > Redacción).
"""
from __future__ import annotations

import asyncio

import pytest

from services import scores24_scraper as s24


@pytest.fixture(autouse=True)
def _clean_cache():
    s24.cache_clear()
    yield
    s24.cache_clear()


# ─────────────────────────────────────────────────────────────────────
# Bet extraction (unit-level)
# ─────────────────────────────────────────────────────────────────────
def test_extract_corners_under_with_odds():
    text = (
        "Evalúa la apuesta por el under 9.5 córners totales, una línea que se "
        "ha cumplido en los últimos cinco partidos de México como local. Cuota: 1.58*."
    )
    out = s24._extract_explicit_bet(text)
    assert out["market_type"] == "corners_total"
    assert out["side"] == "UNDER"
    assert out["line"] == 9.5
    assert out["odds"] == 1.58
    assert "SCORES24_CORNERS_PREDICTION_FOUND" in out["reason_codes"]
    assert "SCORES24_CORNERS_UNDER_LINE_FOUND" in out["reason_codes"]
    assert "SCORES24_ODDS_FOUND" in out["reason_codes"]
    assert "córner" in (out["recommended_market"] or "").lower()


def test_extract_over_1_5_goles_with_spanish_phrase():
    text = (
        "La opción más coherente parece ser el over 1.5 goles totales, con una cuota "
        "cercana a 1.35*."
    )
    out = s24._extract_explicit_bet(text)
    assert out["market_type"] == "goals_total"
    assert out["side"] == "OVER"
    assert out["line"] == 1.5
    assert out["odds"] == 1.35


def test_extract_handicap_minus_one():
    text = (
        "México llega como un conjunto más sólido y equilibrado. "
        "Predicción de la redacción: victoria de México con hándicap (-1). "
        "El resultado más probable es un 2-0."
    )
    out = s24._extract_explicit_bet(text)
    assert out["market_type"] == "handicap"
    assert out["line"] == -1.0
    assert "SCORES24_HANDICAP_FOUND" in out["reason_codes"]


def test_extract_no_explicit_bet_returns_empty_audit():
    text = "México llevará la iniciativa y buscará profundidad por bandas."
    out = s24._extract_explicit_bet(text)
    assert out["recommended_market"] is None
    assert out["side"] is None
    assert out["line"] is None
    assert out["odds"] is None


def test_explicit_bet_overrides_narrative_inference():
    """Critical rule: even if context mentions 'profundidad por bandas',
    the explicit 'under 9.5' must produce UNDER 9.5, not OVER."""
    text = (
        "Todo apunta a que México llevará la iniciativa y buscará profundidad por "
        "bandas, pero esto no garantiza un alto número de remates. "
        "Merece atención la apuesta por el under 9.5 córners totales, una línea "
        "que se ha cumplido en los últimos cinco partidos. Cuota: 1.58*."
    )
    out = s24._extract_explicit_bet(text)
    assert out["side"] == "UNDER"
    assert out["line"] == 9.5
    assert out["market_type"] == "corners_total"


# ─────────────────────────────────────────────────────────────────────
# Section extraction (BS4 path)
# ─────────────────────────────────────────────────────────────────────
_FIXTURE_HTML = """
<html><body>
<h2>Predicción sobre córners</h2>
<p>Todo apunta a que México llevará la iniciativa y buscará profundidad por bandas,
pero esto no garantiza un alto número de remates ni una defensa sudafricana
desbordada. Merece atención la apuesta por el under 9.5 córners totales, una línea
que se ha cumplido en los últimos cinco partidos de México como local. Cuota: 1.58*.</p>
<h2>Apuesta fiable</h2>
<p>La opción más coherente parece ser el over 1.5 goles totales, con una cuota
cercana a 1.35*. México es claro favorito.</p>
<h3>Predicción de la redacción</h3>
<p>México llega como un conjunto más sólido y equilibrado. Predicción de la redacción:
victoria de México con hándicap (-1). El resultado más probable es un 2-0.</p>
</body></html>
"""


def test_extract_sections_from_html_three_sections_found():
    sections = s24._extract_sections_from_html(_FIXTURE_HTML)
    keys = {s["section"] for s in sections}
    assert keys == {"corners_prediction", "apuesta_fiable", "prediccion_redaccion"}


def test_build_section_payload_for_corners():
    sections = s24._extract_sections_from_html(_FIXTURE_HTML)
    corners = next(s for s in sections if s["section"] == "corners_prediction")
    payload = s24._build_section_payload(corners)
    assert payload["section"] == "corners_prediction"
    assert payload["side"] == "UNDER"
    assert payload["line"] == 9.5
    assert payload["odds"] == 1.58
    assert payload["market_type"] == "corners_total"
    # narrative_context should be the prose without the explicit bet.
    assert payload["narrative_context"] and "[BET]" in payload["narrative_context"]


def test_extract_sections_empty_when_no_match():
    assert s24._extract_sections_from_html("") == []
    assert s24._extract_sections_from_html("<html></html>") == []


# ─────────────────────────────────────────────────────────────────────
# End-to-end scrape with injected fetcher (no real network)
# ─────────────────────────────────────────────────────────────────────
def test_scrape_scores24_match_happy_path_with_fixture():
    async def _fake_fetcher(_url):  # noqa: ANN001
        return _FIXTURE_HTML

    payload = asyncio.run(s24.scrape_scores24_match(
        url="https://scores24.live/es/soccer/m-11-06-2026-mexico-south-africa-prediction",
        use_cache=False,
        fetcher=_fake_fetcher,
    ))
    assert payload["available"] is True
    assert payload["source"] == "scores24:web_unlocker1"
    assert len(payload["sections"]) == 3
    cons = payload["consensus"]
    # "Apuesta fiable" has higher priority than corners → primary is over 1.5 goles.
    assert cons["primary_section"] == "apuesta_fiable"
    assert cons["primary_market_type"] == "goals_total"
    assert cons["primary_side"] == "OVER"
    assert cons["primary_line"] == 1.5
    assert "SCORES24_PRIMARY_MARKET_IDENTIFIED" in payload["reason_codes"]


def test_scrape_scores24_match_failsoft_when_fetcher_returns_none():
    async def _fail(_url):  # noqa: ANN001
        return None
    payload = asyncio.run(s24.scrape_scores24_match(
        url="https://scores24.live/es/soccer/match-x",
        use_cache=False,
        fetcher=_fail,
    ))
    assert payload["available"] is False
    assert payload["source"] == "unavailable"
    assert payload["sections"] == []
    assert "SCORES24_FETCH_FAILED" in payload["reason_codes"]


def test_scrape_scores24_match_invalid_url():
    payload = asyncio.run(s24.scrape_scores24_match(url=""))
    assert payload["available"] is False
    assert "SCORES24_INVALID_URL" in payload["reason_codes"]


def test_scrape_scores24_match_cache_hit():
    call_count = {"n": 0}

    async def _fetcher(_url):  # noqa: ANN001
        call_count["n"] += 1
        return _FIXTURE_HTML

    url = "https://scores24.live/es/soccer/cache-test"
    r1 = asyncio.run(s24.scrape_scores24_match(url=url, fetcher=_fetcher))
    r2 = asyncio.run(s24.scrape_scores24_match(url=url, fetcher=_fetcher))
    assert call_count["n"] == 1
    assert r1["available"] == r2["available"]


# ─────────────────────────────────────────────────────────────────────
# Integration helper smoke
# ─────────────────────────────────────────────────────────────────────
def test_integration_attach_scores24_to_pick_payload(monkeypatch):
    from services import scores24_enrichment_integration as integ

    async def _fake_scrape(*, url, use_cache=True):  # noqa: ANN001
        return {
            "available":      True,
            "engine_version": "test",
            "url":             url,
            "source":          "scores24:web_unlocker1",
            "fetched_at":      "2026-06-11T00:00:00Z",
            "sections":        [{"section": "corners_prediction"}],
            "consensus":       {"primary_market_type": "corners_total"},
            "reason_codes":    ["SCORES24_FETCH_OK"],
        }

    monkeypatch.setattr("services.scores24_scraper.scrape_scores24_match", _fake_scrape)

    pick = {"recommendation": {"market": "OVER_2_5", "confidence_score": 70.0}}
    audit = asyncio.run(integ.attach_scores24_to_pick_payload(
        pick, scores24_url="https://scores24.live/test", use_cache=False,
    ))
    assert audit["available"] is True
    # ENRICHMENT-ONLY contract: must NOT have mutated the recommendation.
    assert pick["recommendation"]["market"] == "OVER_2_5"
    assert pick["recommendation"]["confidence_score"] == 70.0
    # Audit attached.
    assert pick["scores24_enrichment"]["available"] is True
    # CamelCase mirror present.
    assert pick["footballHistoricalProfile"]["scores24Enrichment"]["available"] is True


def test_integration_failsoft_when_no_url():
    from services import scores24_enrichment_integration as integ
    pick = {"recommendation": {"market": "OVER_2_5"}}
    audit = asyncio.run(integ.attach_scores24_to_pick_payload(
        pick, scores24_url=None,
    ))
    assert audit["available"] is False
    assert audit["_reason"] == "no_url"
    assert pick["scores24_enrichment"]["available"] is False


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])
