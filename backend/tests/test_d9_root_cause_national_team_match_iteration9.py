"""Sprint-D9-RootCauseFix · Test del fix para el bug
NO_PRIORITY_FIXTURES_FOUND.

Root cause: ``is_national_team_match`` no manejaba el shape de los
documentos en ``db.matches`` (donde ``league`` es STRING, no dict, y
``league_id`` es el ID de TheSportsDB que NO está en
``NATIONAL_TEAM_LEAGUES``).

Fix: ahora honra:
  1. Flags pre-calculados ``is_national_team=True`` /
     ``competition_type=="international"``.
  2. ``league`` como string directo (no solo dict).
  3. ``competition_canonical_name`` (post-classifier).
"""
from __future__ import annotations


def test_match_doc_shape_from_db_matches_with_flag_is_national_team():
    """Reproducción exacta del shape persistido en db.matches para
    partidos WC ingresados por TheSportsDB (Sprint-D9 — Uruguay vs
    Cape Verde, New Zealand vs Egypt, etc.)."""
    from services.api_sports import is_national_team_match
    doc = {
        "match_id":          "2391757",
        "home_team":         {"name": "Uruguay"},
        "away_team":         {"name": "Cape Verde"},
        "league":            "FIFA World Cup",   # ← STRING, no dict
        "league_id":         4429,                # ← TheSportsDB id (no canónico AF)
        "league_name":       None,
        "is_national_team":  True,
        "is_international":  True,
        "competition_canonical_name": "FIFA World Cup",
        "competition_tier":  "tier_1",
        "competition_type":  "international",
    }
    assert is_national_team_match(doc) is True


def test_match_doc_shape_with_string_league_only_no_flags():
    """Caso defensivo: solo string league, sin flags pre-calculados."""
    from services.api_sports import is_national_team_match
    doc = {
        "match_id": "X",
        "league": "FIFA World Cup",
        "league_id": 4429,
    }
    assert is_national_team_match(doc) is True


def test_match_doc_with_competition_canonical_name_only():
    """Algunos paths persisten solo ``competition_canonical_name``."""
    from services.api_sports import is_national_team_match
    doc = {
        "match_id": "X",
        "competition_canonical_name": "UEFA Nations League",
        "competition_type": "international",
    }
    assert is_national_team_match(doc) is True


def test_legacy_api_football_dict_shape_still_works():
    """Regresión: el shape original (league como dict) sigue OK."""
    from services.api_sports import is_national_team_match
    doc = {
        "league": {"id": 10, "name": "International Friendlies"},
    }
    assert is_national_team_match(doc) is True


def test_clubs_in_db_matches_shape_correctly_rejected():
    """Partido de club en shape db.matches NO debe matchearse."""
    from services.api_sports import is_national_team_match
    doc = {
        "match_id":  "555",
        "league":    "Premier League",
        "league_id": 39,
        "is_national_team": False,
        "competition_canonical_name": "Premier League",
        "competition_tier": "tier_1",
        "competition_type": "domestic_league",  # NOT international
    }
    assert is_national_team_match(doc) is False


def test_string_league_with_unknown_name_falls_through_to_false():
    """League string desconocida → False (no false-positive)."""
    from services.api_sports import is_national_team_match
    doc = {
        "league": "Some Random Local Cup",
        "league_id": 99999,
    }
    assert is_national_team_match(doc) is False


def test_is_national_team_flag_overrides_unknown_league_name():
    """Si el flag está en True, confiamos en él aunque el name sea
    desconocido (el classifier ya tomó la decisión)."""
    from services.api_sports import is_national_team_match
    doc = {
        "league": "Some Exotic Friendly League",
        "league_id": 12345,
        "is_national_team": True,
    }
    assert is_national_team_match(doc) is True
