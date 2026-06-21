"""Sprint-D9-HOTFIX · Tests para los 3 fixes del post-deploy report:
  1. ``is_national_team_match`` — matching por ID y por nombre.
  2. ``discover_priority_fixtures`` — descarta matches by-name-only
     cuando matched_by_id=0 y delega a API-Football.
  3. Forebet parser — parser estructural usando los selectores HTML
     actuales (homeTeam / awayTeam / fpr / forepr / etc.).
"""
from __future__ import annotations


# ─────────────────────────────────────────────────────────────────────
# 1) is_national_team_match — combina chequeo por ID + por nombre
# ─────────────────────────────────────────────────────────────────────
def test_is_national_team_match_by_id():
    from services.api_sports import is_national_team_match
    # API-Football canonical IDs (10 = International Friendlies)
    assert is_national_team_match({"league_id": 10}) is True
    assert is_national_team_match({"league": {"id": 4}}) is True  # Euro
    # Liga de clubes
    assert is_national_team_match({"league_id": 39}) is False  # Premier
    assert is_national_team_match({"league": {"id": 140}}) is False  # LaLiga


def test_is_national_team_match_by_name_when_id_unknown():
    """Cuando el league_id no es canónico (p.ej. TheSportsDB id 4429),
    pero el nombre matchea con un torneo de selecciones, debe
    detectarse como tal."""
    from services.api_sports import is_national_team_match
    # TheSportsDB usa id=4429 para "FIFA World Cup" (sub-17 a veces).
    # Aunque ese ID no esté en NATIONAL_TEAM_LEAGUES, el nombre sí
    # matchea con la heurística.
    assert is_national_team_match(
        {"league_id": 4429, "league_name": "FIFA World Cup"}
    ) is True
    # Variantes:
    for name in (
        "International Friendlies", "International Friendly",
        "UEFA Nations League", "Nations League",
        "UEFA Euro 2024", "European Championship",
        "Copa America", "Copa América",
        "Africa Cup of Nations", "AFCON Qualifiers",
        "World Cup Qualification CONMEBOL",
        "Asian Cup", "CONCACAF Gold Cup",
    ):
        assert is_national_team_match({"league": {"id": 99999, "name": name}}) is True, (
            f"Expected {name} to be detected as national team league"
        )


def test_is_national_team_match_rejects_clubs_by_name():
    from services.api_sports import is_national_team_match
    for name in (
        "Premier League", "Bundesliga", "LaLiga", "Serie A",
        "Brazilian Serie B", "USL League Two", "Argentinian Primera B Nacional",
    ):
        assert is_national_team_match({"league": {"id": 99999, "name": name}}) is False


def test_is_national_team_match_fail_soft_on_garbage():
    from services.api_sports import is_national_team_match
    assert is_national_team_match(None) is False
    assert is_national_team_match("string") is False
    assert is_national_team_match({}) is False
    assert is_national_team_match({"league": "not a dict"}) is False
    assert is_national_team_match({"league": {"id": "abc"}}) is False


# ─────────────────────────────────────────────────────────────────────
# 2) discover_priority_fixtures — matched_by_id_count gating
# ─────────────────────────────────────────────────────────────────────
def test_priority_discovery_priority_ladder_constants_still_present():
    """Smoke check: la PRIORITY_LADDER no fue alterada accidentalmente
    por el hotfix."""
    import inspect
    from services import data_ingestion as di
    src = inspect.getsource(di.discover_priority_fixtures)
    # Debe seguir contemplando estas ligas insignia.
    for marker in (
        "UEFA Champions League",
        "Premier League",
        "LaLiga",
        "Bundesliga",
        "Serie A",
    ):
        assert marker in src, f"PRIORITY_LADDER missing {marker} after hotfix"
    # Y el filtro `matched_by_id_count == 0` debe estar.
    assert "matched_by_id_count" in src


# ─────────────────────────────────────────────────────────────────────
# 3) Forebet parser — estructural (nuevos selectores)
# ─────────────────────────────────────────────────────────────────────
def test_forebet_parser_structured_extracts_team_with_space():
    """Regresión: "New Zealand vs Egypt" debe parsearse como
    home="New Zealand" / away="Egypt", NO como "New" / "Zealand Egypt"
    (bug observado tras el redesign del HTML de Forebet)."""
    from services.forebet_scraper import parse_forebet_fixtures_page

    # HTML mínimo reproduciendo la estructura nueva de Forebet (un row).
    html = """
    <html><body>
    """ + ("x" * 1100) + """
    <div class='rcnt tr_1'>
       <div class="stcn">
          <div class="shortagDiv tghov">
             <span class="shortTag">WC</span>
          </div>
       </div>
       <div class="tnms"><div>
          <a class="tnmscn" href="/es/football/matches/new-zealand-egypt-2463168">
             <span class="homeTeam"><span>New Zealand</span></span>
             <span class="awayTeam"><span>Egypt</span></span>
             <span class="date_bah">22/06/2026 03:00</span>
          </a>
       </div></div>
       <div class='fprc'>
          <span class="fpr">25</span>
          <span>44</span>
          <span>31</span>
       </div>
       <div class="predict_no">
          <span class="forepr"><span>X</span></span>
          <span class="scrmobpred ex_sc">1<span class="scrmobpreddash">-</span>1</span>
       </div>
       <div class="ex_sc tabonly">1 - 1</div>
       <div class="avg_sc exact_yes tabonly">2.10</div>
    </div>
    </body></html>
    """

    out = parse_forebet_fixtures_page(html)
    assert out["available"] is True
    assert len(out["fixtures"]) == 1
    fx = out["fixtures"][0]
    assert fx["home_team"] == "New Zealand"
    assert fx["away_team"] == "Egypt"
    assert fx["forebet_pct_1"] == 25
    assert fx["forebet_pct_x"] == 44
    assert fx["forebet_pct_2"] == 31
    assert fx["pick_1x2"] == "X"
    assert fx["predicted_score"] == "1-1"
    assert abs(fx["goals_avg"] - 2.10) < 0.01
    assert fx["match_url"] == "https://www.forebet.com/es/football/matches/new-zealand-egypt-2463168"
    assert fx["_parser"] == "structured"


def test_forebet_parser_handles_team_with_special_chars():
    """Reproducción del row "Ecuador vs Curaçao" — diacríticos OK."""
    from services.forebet_scraper import parse_forebet_fixtures_page

    html = """
    <html><body>
    """ + ("x" * 1100) + """
    <div class='rcnt tr_1'>
       <div class="stcn"><div class="shortagDiv"><span class="shortTag">WC</span></div></div>
       <div class="tnms"><div>
          <a class="tnmscn" href="/es/football/matches/ecuador-curacao-2463163">
             <span class="homeTeam"><span>Ecuador</span></span>
             <span class="awayTeam"><span>Curaçao</span></span>
             <span class="date_bah">21/06/2026 02:00</span>
          </a>
       </div></div>
       <div class='fprc'>
          <span class="fpr">64</span>
          <span>20</span>
          <span>16</span>
       </div>
       <span class="forepr"><span>1</span></span>
       <span class="scrmobpred ex_sc">2<span>-</span>0</span>
       <div class="avg_sc tabonly">1.75</div>
    </div>
    </body></html>
    """
    out = parse_forebet_fixtures_page(html)
    fx = out["fixtures"][0]
    assert fx["home_team"] == "Ecuador"
    assert fx["away_team"] == "Curaçao"
    assert fx["forebet_pct_1"] == 64
