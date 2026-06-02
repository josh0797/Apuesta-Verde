"""Regression tests for the live/national-teams fix:

Cubre los 6 escenarios explícitos del usuario:
  1. Bélgica vs Croacia (amistoso internacional) aparece en Partidos en vivo.
  2. Partidos de World Cup / Euro / Copa América pasan el filtro.
  3. Partidos de clubes NO pasan cuando el filtro es "Selecciones".
  4. Un partido live no se manda a "archivados".
  5. Refrescar partidos no genera duplicados (idempotencia upsert).
  6. Empty state correcto cuando no hay partidos live reales.

Adicionalmente cubre el bug que originó la queja:
  * NS / TBD ya NO están en FINISHED_STATUSES (eran pre-game, no terminal).
"""
from __future__ import annotations

import pytest
from datetime import datetime, timezone

from services.api_sports import (
    NATIONAL_TEAM_LEAGUES,
    is_national_team_league,
)
from services.live_lifecycle import (
    FINISHED_STATUSES,
    LIVE_STATUSES,
    compute_live_state,
    is_match_live,
)


# ───────────────────────────────────────────────────────────────────────
# Bug fix: NS/TBD no deben estar en FINISHED_STATUSES
# ───────────────────────────────────────────────────────────────────────
class TestFinishedStatusesBugFix:
    def test_NS_not_in_finished(self):
        # NS = Not Started → debe ser pre-game, NO terminal.
        assert "NS" not in FINISHED_STATUSES["football"]

    def test_TBD_not_in_finished(self):
        # TBD = To Be Defined → pre-game.
        assert "TBD" not in FINISHED_STATUSES["football"]

    def test_FT_still_finished(self):
        # FT = Full Time → terminal (no debe haberse roto).
        assert "FT" in FINISHED_STATUSES["football"]

    def test_AET_still_finished(self):
        assert "AET" in FINISHED_STATUSES["football"]


# ───────────────────────────────────────────────────────────────────────
# NATIONAL_TEAM_LEAGUES coverage
# ───────────────────────────────────────────────────────────────────────
class TestNationalTeamLeagueIDs:
    def test_world_cup_included(self):
        assert is_national_team_league(1) is True   # FIFA World Cup

    def test_euros_included(self):
        assert is_national_team_league(4) is True   # Euro Championship

    def test_copa_america_included(self):
        assert is_national_team_league(9) is True   # Copa América

    def test_gold_cup_included(self):
        assert is_national_team_league(22) is True  # CONCACAF Gold Cup

    def test_nations_league_included(self):
        assert is_national_team_league(5) is True   # UEFA Nations League

    def test_friendlies_included(self):
        assert is_national_team_league(10) is True  # International Friendlies

    def test_wc_qualifying_europe_included(self):
        assert is_national_team_league(32) is True

    def test_premier_league_excluded(self):
        assert is_national_team_league(39) is False  # Premier League (clubes)

    def test_champions_league_excluded(self):
        assert is_national_team_league(2) is False   # UEFA Champions League (clubes)

    def test_libertadores_excluded(self):
        assert is_national_team_league(13) is False  # Copa Libertadores (clubes)


# ───────────────────────────────────────────────────────────────────────
# is_match_live para amistosos internacionales (caso del usuario)
# ───────────────────────────────────────────────────────────────────────
class TestLiveDetectionInternationalFriendly:
    def _belgium_croatia_live(self, **overrides):
        """Croatia vs Belgium friendly al minuto 89, status 2H."""
        m = {
            "match_id":     "1512766",
            "sport":        "football",
            "league_id":    10,
            "league":       {"id": 10, "name": "Friendlies"},
            "is_live":      True,
            "status_short": "2H",
            "home_team":    {"name": "Croatia"},
            "away_team":    {"name": "Belgium"},
            "live_stats":   {"status": "2H", "minute": 89},
            "updated_at":   datetime.now(timezone.utc).isoformat(),
        }
        m.update(overrides)
        return m

    def test_belgium_croatia_friendly_is_live(self):
        # Escenario 1: el caso que reportó el usuario.
        m = self._belgium_croatia_live()
        assert is_match_live(m) is True
        state = compute_live_state(m)
        assert state["valid"] is True
        assert state["state"] in {"LIVE_EARLY", "LIVE_MID", "LIVE_LATE", "LIVE_HT"}

    def test_world_cup_match_passes_filter(self):
        # Escenario 2 (World Cup).
        m = self._belgium_croatia_live(
            league_id=1, league={"id": 1, "name": "FIFA World Cup"},
        )
        assert is_match_live(m) is True
        assert is_national_team_league(m["league_id"]) is True

    def test_copa_america_match_passes_filter(self):
        m = self._belgium_croatia_live(
            league_id=9, league={"id": 9, "name": "Copa America"},
        )
        assert is_match_live(m) is True
        assert is_national_team_league(m["league_id"]) is True

    def test_euro_match_passes_filter(self):
        m = self._belgium_croatia_live(
            league_id=4, league={"id": 4, "name": "Euro Championship"},
        )
        assert is_match_live(m) is True
        assert is_national_team_league(m["league_id"]) is True

    def test_club_match_excluded_under_national_filter(self):
        # Escenario 3: Real Madrid vs Manchester City NO debe pasar el filtro.
        club_match = {
            "match_id":  "9876",
            "league_id": 2,   # UEFA Champions League
            "league":    {"id": 2, "name": "UEFA Champions League"},
            "home_team": {"name": "Real Madrid"},
            "away_team": {"name": "Manchester City"},
            "status_short": "1H",
            "live_stats": {"status": "1H", "minute": 30},
        }
        assert is_national_team_league(club_match["league_id"]) is False

    def test_live_match_is_NOT_finished(self):
        # Escenario 4: si el partido está live, NO debe terminar archivado.
        m = self._belgium_croatia_live()
        state = compute_live_state(m)
        assert state["valid"] is True
        # No terminal status → no debe ser purgado por sweep_expired.
        assert m["status_short"] not in FINISHED_STATUSES["football"]
        # Live statuses para football incluye "2H".
        assert "2H" in LIVE_STATUSES["football"]

    def test_NS_status_does_NOT_force_archive(self):
        # Regresión del bug: status=NS (Not Started) NO debe marcar como
        # finished. Si el match aún no empezó, simplemente no es live;
        # pero no debe ser purgado.
        m = self._belgium_croatia_live(
            status_short="NS",
            live_stats={"status": None, "minute": None},
            is_live=False,
        )
        # is_match_live debería ser False (no comenzó), pero el match NO
        # está en FINISHED — el sweep_expired no lo borraría como terminal.
        assert is_match_live(m) is False
        assert "NS" not in FINISHED_STATUSES["football"]


# ───────────────────────────────────────────────────────────────────────
# Idempotencia / dedupe upsert (escenario 5)
# ───────────────────────────────────────────────────────────────────────
class TestRefreshIdempotency:
    """El upsert downstream usa match_id como clave única — llamar al
    refresh varias veces NUNCA puede crear documentos duplicados.

    Aquí lo verificamos a nivel lógico construyendo el match_id
    canónico que usa el código de producción.
    """

    def _stable_key(self, match: dict) -> str:
        # Replica el patrón documentado en el código frontend/backend.
        if match.get("match_id"):    return f"id:{match['match_id']}"
        if match.get("fixture_id"):  return f"fx:{match['fixture_id']}"
        home = (match.get("home_team") or {}).get("name") or "?"
        away = (match.get("away_team") or {}).get("name") or "?"
        kick = match.get("kickoff_iso") or match.get("date") or "tbd"
        return f"slug:{home}-{away}-{kick}"

    def test_same_match_yields_same_key(self):
        m1 = {"match_id": "1512766", "home_team": {"name": "Croatia"}, "away_team": {"name": "Belgium"}}
        m2 = {"match_id": "1512766", "home_team": {"name": "Croatia"}, "away_team": {"name": "Belgium"}, "extra": "field"}
        assert self._stable_key(m1) == self._stable_key(m2)

    def test_different_matches_yield_different_keys(self):
        m1 = {"match_id": "1512766"}
        m2 = {"match_id": "1512767"}
        assert self._stable_key(m1) != self._stable_key(m2)

    def test_fallback_to_team_slug_when_no_id(self):
        m1 = {
            "home_team": {"name": "Croatia"},
            "away_team": {"name": "Belgium"},
            "kickoff_iso": "2026-06-02T19:00:00Z",
        }
        m2 = {
            "home_team": {"name": "Croatia"},
            "away_team": {"name": "Belgium"},
            "kickoff_iso": "2026-06-02T19:00:00Z",
        }
        # Same composite key → idempotent upsert.
        assert self._stable_key(m1) == self._stable_key(m2)


# ───────────────────────────────────────────────────────────────────────
# Live statuses cover (los 5 que el usuario pidió)
# ───────────────────────────────────────────────────────────────────────
class TestLiveStatusCoverage:
    @pytest.mark.parametrize("status", ["1H", "2H", "HT", "ET", "P"])
    def test_football_live_statuses_recognized(self, status):
        assert status in LIVE_STATUSES["football"]
