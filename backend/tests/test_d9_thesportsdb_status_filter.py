"""Sprint-D9 · Filtros de status en cascada de fixtures discovery.

Verifica el fix del bug histórico "Ecuador vs Curaçao status=FT
apareciendo como upcoming":

  * ``_normalize_to_apifootball_shape`` descarta eventos con status FT
    (Match Finished), AET, PEN, CANC, ABD, así como status en vivo
    (1H, HT, 2H, ET, ...). Solo NS / TBD pasan.
  * ``_canonical_status`` mapea correctamente los textos en español
    e inglés de TheSportsDB.
  * El filtro defensivo en ``_discover_football_fixtures`` (vía
    ``_F87_VALID_UPCOMING_STATUSES``) descarta cualquier status que
    se cuele desde otra source futura.
"""
from __future__ import annotations

import pytest

from services.external_sources import thesportsdb_fixtures_adapter as adp


# ---------------- _canonical_status ----------------

def test_canonical_status_maps_finished_texts():
    assert adp._canonical_status("Match Finished") == "FT"
    assert adp._canonical_status("Finished") == "FT"
    assert adp._canonical_status("FT") == "FT"
    assert adp._canonical_status("AET") == "AET"
    assert adp._canonical_status("Penalty Shootout") == "PEN"


def test_canonical_status_maps_live_texts():
    assert adp._canonical_status("First Half") == "1H"
    assert adp._canonical_status("Halftime") == "HT"
    assert adp._canonical_status("Second Half") == "2H"


def test_canonical_status_maps_upcoming_texts():
    assert adp._canonical_status("Not Started") == "NS"
    assert adp._canonical_status("NS") == "NS"
    assert adp._canonical_status("TBD") == "TBD"


def test_canonical_status_handles_none_and_empty():
    assert adp._canonical_status(None) == "NS"
    assert adp._canonical_status("") == "NS"


# ---------------- _normalize_to_apifootball_shape ----------------

def _ev(status: str, **overrides) -> dict:
    base = {
        "event_id":  "12345",
        "home_team": {"id": 1, "name": "Spain"},
        "away_team": {"id": 2, "name": "Saudi Arabia"},
        "league_name": "FIFA World Cup",
        "league_id":   None,
        "date_event":  "2025-11-21",
        "event_time":  "20:00:00",
        "timestamp":   None,
        "status":      status,
        "season":      "2025",
    }
    base.update(overrides)
    return base


def test_normalize_drops_finished_events():
    """Ecuador vs Curaçao FT (Match Finished) NO debe pasar."""
    ev = _ev("Match Finished",
              home_team={"id": 10, "name": "Ecuador"},
              away_team={"id": 11, "name": "Curaçao"})
    assert adp._normalize_to_apifootball_shape(ev) is None


def test_normalize_drops_live_events():
    """Partido en 1H tampoco debe pasar."""
    ev = _ev("First Half")
    assert adp._normalize_to_apifootball_shape(ev) is None


def test_normalize_drops_cancelled_events():
    ev = _ev("Cancelled")
    assert adp._normalize_to_apifootball_shape(ev) is None


def test_normalize_keeps_not_started_events():
    """Spain vs Saudi Arabia NS sí debe pasar."""
    ev = _ev("Not Started")
    fx = adp._normalize_to_apifootball_shape(ev)
    assert fx is not None
    assert fx["fixture"]["status"]["short"] == "NS"
    assert fx["teams"]["home"]["name"] == "Spain"
    assert fx["_discovery_source"] == "thesportsdb"


def test_normalize_keeps_tbd_events():
    ev = _ev("TBD")
    fx = adp._normalize_to_apifootball_shape(ev)
    assert fx is not None
    assert fx["fixture"]["status"]["short"] == "TBD"


def test_normalize_drops_when_team_names_missing():
    ev = _ev("Not Started", home_team={"id": 1, "name": ""})
    assert adp._normalize_to_apifootball_shape(ev) is None


# ---------------- fetch_fixtures_next_48h reason codes ----------------

@pytest.mark.asyncio
async def test_fetch_reports_filtered_count(monkeypatch):
    """Si TheSportsDB devuelve 3 NS + 2 FT, el adapter debe filtrar
    los FT y reportar el contador en reason_codes."""
    from services.external_sources import thesportsdb_client as tsdb

    monkeypatch.setattr(tsdb, "is_enabled", lambda: True)

    mock_items_today = [
        {"event_id": "1", "home_team": {"id": 1, "name": "A"},
         "away_team": {"id": 2, "name": "B"}, "league_name": "X",
         "date_event": "2025-11-21", "event_time": "20:00:00",
         "status": "Not Started"},
        {"event_id": "2", "home_team": {"id": 3, "name": "Ecuador"},
         "away_team": {"id": 4, "name": "Curaçao"}, "league_name": "Friendly",
         "date_event": "2025-11-21", "event_time": "01:00:00",
         "status": "Match Finished"},  # ¡debe ser filtrado!
        {"event_id": "3", "home_team": {"id": 5, "name": "C"},
         "away_team": {"id": 6, "name": "D"}, "league_name": "Y",
         "date_event": "2025-11-21", "event_time": "22:00:00",
         "status": "NS"},
    ]
    mock_items_tomorrow = [
        {"event_id": "4", "home_team": {"id": 7, "name": "Tunisia"},
         "away_team": {"id": 8, "name": "Japan"}, "league_name": "Friendly",
         "date_event": "2025-11-22", "event_time": "12:00:00",
         "status": "Match Finished"},  # filtrado
        {"event_id": "5", "home_team": {"id": 9, "name": "E"},
         "away_team": {"id": 10, "name": "F"}, "league_name": "Z",
         "date_event": "2025-11-22", "event_time": "15:00:00",
         "status": "NS"},
    ]

    call_state = {"n": 0}

    async def _mock_fetch(*, date, sport, client):
        call_state["n"] += 1
        items = mock_items_today if call_state["n"] == 1 else mock_items_tomorrow
        return {"available": True, "items": items, "reason_codes": ["OK"]}

    monkeypatch.setattr(tsdb, "fetch_upcoming_events_by_date", _mock_fetch)

    fixtures, codes = await adp.fetch_fixtures_next_48h()

    # 3 deben pasar (2 today NS + 1 tomorrow NS), 2 filtrados
    assert len(fixtures) == 3
    statuses = [((fx.get("fixture") or {}).get("status") or {}).get("short")
                  for fx in fixtures]
    assert all(s == "NS" for s in statuses)
    assert any("THESPORTSDB_FILTERED_FINISHED_OR_LIVE=2" in c for c in codes)
    assert adp.RC_OK in codes


# ---------------- Defensa-en-profundidad en _discover_football_fixtures ----------------

def test_f87_valid_upcoming_statuses_contract():
    """El filtro defensivo solo permite NS/TBD."""
    from services.data_ingestion import _F87_VALID_UPCOMING_STATUSES
    assert _F87_VALID_UPCOMING_STATUSES == frozenset({"NS", "TBD"})


def test_f87_merge_priority_includes_thesportsdb():
    """thesportsdb debe estar en la prioridad de merge (era el bug que
    impedía que fixtures de TheSportsDB se respetaran en el merge final)."""
    from services.data_ingestion import _F87_MERGE_PRIORITY
    assert "thesportsdb" in _F87_MERGE_PRIORITY
    # Y debe ir primero (prioridad más alta)
    assert _F87_MERGE_PRIORITY[0] == "thesportsdb"
