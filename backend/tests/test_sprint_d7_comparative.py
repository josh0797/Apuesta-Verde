"""Sprint-D7 · Tests for the comparative-backtest scaffolding.

All tests are **offline**: they inject a fake transport into the
historical client and a fake picks list into the orchestrator. No
network, no credit spend.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any
from pathlib import Path
from unittest.mock import patch

import pytest

from services import theoddsapi_historical_client as histc
from scripts import run_backtest_d7_comparative as orch


# ════════════════════════════════════════════════════════════════════════
# Fake HTTP transport
# ════════════════════════════════════════════════════════════════════════
def _make_http(events_per_date=1, used_start=100, used_step=10,
                fail_after=None):
    """Build an async fake HTTP that increments x-requests-used on every
    call. ``fail_after`` (int) returns a 502 after N calls."""
    state = {"calls": 0, "used": used_start}

    async def _http(url, params, **kwargs):
        state["calls"] += 1
        if fail_after is not None and state["calls"] > fail_after:
            return {"ok": False, "status": 502, "json": None,
                    "headers": {"x-requests-used": str(state["used"])}}
        # Events listing call.
        if "/events?" not in url and url.endswith("/events"):
            state["used"] += used_step
            data = [
                {"id": f"evt-{i}-{state['calls']}",
                 "commence_time": "2024-06-20T15:00:00Z",
                 "home_team": "A", "away_team": "B"}
                for i in range(events_per_date)
            ]
            return {"ok": True, "status": 200,
                    "json": {"data": data, "timestamp": "x"},
                    "headers": {"x-requests-used": str(state["used"])}}
        # Event odds call (10 credits in reality).
        state["used"] += used_step
        return {"ok": True, "status": 200,
                "json": {"data": {"bookmakers": []}, "timestamp": "y"},
                "headers": {"x-requests-used": str(state["used"])}}
    return _http, state


# ════════════════════════════════════════════════════════════════════════
# 1) Credit-cap aborts before overspend
# ════════════════════════════════════════════════════════════════════════
def test_credit_cap_aborts_before_overspend():
    """Cap=50 → should abort mid-way without raising."""
    http, _state = _make_http(events_per_date=10, used_start=0, used_step=10)
    dates = [f"2024-06-{d:02d}T00:00:00Z" for d in range(1, 6)]
    res = asyncio.run(histc.fetch_tournament_pit_odds(
        sport_key="soccer_test",
        dates_iso=dates, max_credits=50,
        api_key="fake",
        http=http,
    ))
    assert res["aborted"] is True
    assert "MAX_CREDITS_REACHED" in res["reason_codes"]
    assert res["credits_used"] >= 50


# ════════════════════════════════════════════════════════════════════════
# 2) odds_type is marked per block (compile-time invariant)
# ════════════════════════════════════════════════════════════════════════
def test_odds_type_marked_per_block():
    # Domestic uses parse_football_data_csv with prefer_closing=False →
    # OPENING. The orchestrator hard-codes "OPENING" / "POINT_IN_TIME_PREMATCH".
    src = Path(__file__).resolve().parents[1] / "scripts" / "run_backtest_d7_comparative.py"
    code = src.read_text()
    assert "OPENING" in code
    assert "POINT_IN_TIME_PREMATCH" in code


# ════════════════════════════════════════════════════════════════════════
# 3) Combined comparison warns on odds-type mismatch
# ════════════════════════════════════════════════════════════════════════
def test_combined_comparison_warns_on_odds_type_mismatch():
    domestic = {"odds_type": "OPENING",
                 "per_league": {
                     "x": {"available": True,
                           "metrics": {"n_bets": 100, "roi": 0.05,
                                        "hit_rate": 0.30,
                                        "is_roi_significant": False}}}}
    national = {"odds_type": "POINT_IN_TIME_PREMATCH",
                 "per_tournament": {
                     "y": {"available": True,
                           "metrics": {"n_bets": 30, "roi": 0.10,
                                        "hit_rate": 0.40,
                                        "is_roi_significant": True}}}}
    out = orch.build_combined_comparison(domestic, national, all_picks=[])
    assert "W_ODDS_TYPE_MISMATCH" in out["warnings"]


# ════════════════════════════════════════════════════════════════════════
# 4) Cohort defined ONLY by pre-match features (anti-overfitting)
# ════════════════════════════════════════════════════════════════════════
def test_cohort_defined_by_prematch_only():
    """``detect_cohorts`` must never read ``fthg``/``ftag``/``ftr``."""
    import inspect
    from services import football_cohort_detector as fcd
    src = inspect.getsource(fcd.detect_cohorts)
    # The detector receives the features dict — assert it doesn't read
    # post-match keys from the pick either.
    forbidden = ("fthg", "ftag", "ftr", "actual_outcome",
                  "match_result", "_outcome")
    for k in forbidden:
        assert k not in src, f"detect_cohorts must not reference {k!r}"

    # Bonus runtime check: call detect_cohorts with a pick that DOES
    # carry fthg/ftag and confirm the resulting tags don't depend on them.
    pick = {"prediction": 0.34, "edge_pp": 9.0,
             "fthg": 1, "ftag": 1}      # post-match noise
    feats = {"elo_diff": 220, "stage": "GROUP_STAGE",
              "favorite_implied": 0.55}
    tags_a = fcd.detect_cohorts(pick, feats)
    pick_no_post = {k: v for k, v in pick.items()
                     if k not in ("fthg", "ftag")}
    tags_b = fcd.detect_cohorts(pick_no_post, feats)
    assert sorted(tags_a) == sorted(tags_b)


# ════════════════════════════════════════════════════════════════════════
# 5) Pattern not proven when sample is small
# ════════════════════════════════════════════════════════════════════════
def test_pattern_not_proven_when_sample_small():
    domestic = {"odds_type": "OPENING", "per_league": {}}
    national = {"odds_type": "POINT_IN_TIME_PREMATCH",
                 "per_tournament": {}}

    # Inject a fake summary into summarise_picks_by_cohort.
    def _fake_summary(_picks):
        return {
            "DOMINANT_FAVORITE_DRAW_VALUE+TOURNAMENT_GROUP_STAGE_DRAW_VALUE": {
                "n": 7,
                "metrics": {"roi_ci_low": 0.04, "roi": 0.15},
            },
        }
    with patch.object(orch, "summarise_picks_by_cohort", _fake_summary):
        out = orch.build_combined_comparison(domestic, national,
                                                all_picks=[{"_x": 1}])
    assert out["spain_capeverde_pattern"]["status"] == (
        "PATTERN_NOT_YET_PROVEN_INSUFFICIENT_SAMPLE"
    )


def test_pattern_repeatable_with_enough_sample():
    def _fake_summary(_picks):
        return {
            "DOMINANT_FAVORITE_DRAW_VALUE+TOURNAMENT_GROUP_STAGE_DRAW_VALUE": {
                "n": 35,
                "metrics": {"roi_ci_low": 0.07, "roi": 0.18},
            },
        }
    with patch.object(orch, "summarise_picks_by_cohort", _fake_summary):
        out = orch.build_combined_comparison(
            {"odds_type": "OPENING", "per_league": {}},
            {"odds_type": "POINT_IN_TIME_PREMATCH", "per_tournament": {}},
            all_picks=[{"_x": 1}],
        )
    assert out["spain_capeverde_pattern"]["status"] == "PATTERN_REPEATABLE"


# ════════════════════════════════════════════════════════════════════════
# 6) Tournament unavailable does not abort the sprint
# ════════════════════════════════════════════════════════════════════════
def test_national_tournament_unavailable_does_not_abort():
    """Returning an empty events payload must mark the tournament as
    ``UNAVAILABLE_NO_COVERAGE`` without raising or stopping the loop."""
    async def _empty_http(url, params, **kwargs):
        return {"ok": False, "status": 404, "json": None,
                "headers": {"x-requests-used": "200"}}

    res = asyncio.run(histc.fetch_tournament_pit_odds(
        sport_key="soccer_no_coverage",
        dates_iso=["2021-06-15T12:00:00Z"],
        max_credits=200, api_key="fake", http=_empty_http,
    ))
    assert res["events"] == []
    assert "UNAVAILABLE_NO_COVERAGE" in res["reason_codes"]
    assert res["aborted"] is False


# ════════════════════════════════════════════════════════════════════════
# 7) Settlement uses openfootball, NOT The Odds API
# ════════════════════════════════════════════════════════════════════════
def test_settlement_uses_openfootball_not_oddsapi():
    """``_merge_pit_odds_with_truth`` discards any odds-API row that
    lacks a matching openfootball ground-truth row → settlement is
    impossible from the odds payload alone."""
    odds_events = [{
        "event_id": "e1", "home_team": "A", "away_team": "B",
        "event_payload": {"bookmakers": [{
            "markets": [{"key": "h2h", "outcomes": [
                {"name": "A", "price": 2.0},
                {"name": "B", "price": 3.0},
                {"name": "Draw", "price": 3.5},
            ]}],
        }]},
    }]
    # No matching truth row → must drop the event silently.
    assert orch._merge_pit_odds_with_truth(odds_events, []) == []

    truth = [{"home_team": "A", "away_team": "B",
              "fthg": 1, "ftag": 1, "ftr": "D"}]
    merged = orch._merge_pit_odds_with_truth(odds_events, truth)
    assert len(merged) == 1
    # Ground truth came from openfootball, not the odds payload.
    assert merged[0]["fthg"] == 1 and merged[0]["ftag"] == 1
    assert merged[0]["odds_type"] == "POINT_IN_TIME_PREMATCH"


# ════════════════════════════════════════════════════════════════════════
# 8) Cap pre-aborts a future call without re-spending
# ════════════════════════════════════════════════════════════════════════
def test_cap_short_circuits_next_call():
    tracker = histc.CreditTracker(max_credits=10)
    tracker.update(0)
    tracker.update(15)        # already over cap
    assert tracker.must_abort()
    res = asyncio.run(histc.fetch_events_for_date(
        sport_key="x", date_iso="2024-01-01T00:00:00Z",
        tracker=tracker, api_key="k", http=None,
    ))
    assert res["available"] is False
    assert res["reason_code"] == "MAX_CREDITS_REACHED"



# ════════════════════════════════════════════════════════════════════════
# 9) REGRESIÓN BUG #1 — el orquestador debe pasar TEXTO del CSV al parser
# ════════════════════════════════════════════════════════════════════════
def test_domestic_parser_receives_csv_text_not_path(tmp_path):
    """Regresión del bug post-mortem D7.

    Antes del fix, ``run_domestic_block`` llamaba a
    ``parse_football_data_csv(str(csv_path), ...)`` lo que provocaba
    que el parser intentara leer la *ruta* como CSV y devolviera 0
    matches silenciosamente. Tras el fix se lee el archivo con
    ``Path(...).read_text()``.

    El test inyecta un CSV mínimo en una ruta controlada, fuerza solo
    una liga y verifica que ``n_matches > 0`` y ``available=True``.
    """
    csv_text = (
        "Date,HomeTeam,AwayTeam,FTHG,FTAG,FTR,B365H,B365D,B365A\n"
        "16/08/2024,Man United,Fulham,1,0,H,1.80,3.60,4.50\n"
        "17/08/2024,Arsenal,Chelsea,2,2,D,1.95,3.50,4.00\n"
        "18/08/2024,Liverpool,Brighton,2,0,H,1.50,4.20,6.50\n"
    )
    fake_csv = tmp_path / "E0_2425.csv"
    fake_csv.write_text(csv_text)
    # Parche directo a _ensure_league_csv: evita la descarga y nos
    # garantiza la ruta exacta.
    with patch.object(orch, "_ensure_league_csv",
                       lambda code: fake_csv), \
         patch.object(orch, "LEAGUES_2425",
                       [("E0", "premier_league")]):
        block = orch.run_domestic_block(min_edge_pp=4.0)
    league = block["per_league"]["premier_league"]
    assert league["available"] is True, (
        "El parser debe recibir el TEXTO del CSV. Si recibe la ruta, "
        "n_matches=0 y available=False (regresión del bug D7).")
    assert league["n_matches"] == 3
    # Y las métricas se calculan sobre matches reales, no sobre [].
    assert isinstance(league["metrics"], dict)


def test_domestic_parse_empty_marks_available_false(tmp_path):
    """Si el CSV existe pero el parser devuelve 0 matches (CSV vacío
    o sin filas válidas) debe marcarse como ``PARSE_EMPTY`` — no como
    ``available=True n_matches=0`` (regresión silenciosa pre-fix)."""
    fake_csv = tmp_path / "E0_2425.csv"
    # Solo header, sin filas → parser devolverá [].
    fake_csv.write_text("Date,HomeTeam,AwayTeam,FTHG,FTAG,FTR\n")
    with patch.object(orch, "_ensure_league_csv",
                       lambda code: fake_csv), \
         patch.object(orch, "LEAGUES_2425",
                       [("E0", "premier_league")]):
        block = orch.run_domestic_block(min_edge_pp=4.0)
    league = block["per_league"]["premier_league"]
    assert league["available"] is False
    assert league["reason_code"] == "PARSE_EMPTY"


# ════════════════════════════════════════════════════════════════════════
# 10) BUG #3 — propagación real de reason_codes en el bloque nacional
# ════════════════════════════════════════════════════════════════════════
def test_national_block_propagates_real_reason_codes():
    """Cuando ``fetch_tournament_pit_odds`` reporta
    ``MAX_CREDITS_REACHED``, el bloque nacional debe propagarlo en
    ``reason_code`` (no machacarlo con ``UNAVAILABLE_NO_COVERAGE``)."""
    async def _fake_fetch(**kwargs):
        return {
            "available":    False,
            "events":       [],
            "credits_used": 42,
            "aborted":      True,
            "reason_codes": ["MAX_CREDITS_REACHED"],
        }
    with patch.object(orch, "fetch_tournament_pit_odds", _fake_fetch):
        res = asyncio.run(orch.run_national_block(
            max_credits=100, min_edge_pp=4.0,
        ))
    # Primer torneo debe llevar el reason_code real.
    first_label = orch.NATIONAL_TOURNAMENTS[0][0]
    entry = res["per_tournament"][first_label]
    assert entry["available"] is False
    assert entry["reason_code"] == "MAX_CREDITS_REACHED"
    assert "MAX_CREDITS_REACHED" in (entry.get("all_reason_codes") or [])


def test_national_block_marks_ground_truth_missing_distinctly():
    """Si llegan eventos pero NO existe el JSON openfootball, el
    bloque debe marcar ``GROUND_TRUTH_MISSING`` con auditoría
    explícita."""
    async def _fake_fetch(**kwargs):
        return {
            "available":    True,
            "events":       [{
                "id": "evt-1", "home_team": "Spain",
                "away_team": "Cape Verde",
                "commence_time": "2022-11-27T15:00:00Z",
                "event_payload": {"bookmakers": [{
                    "markets": [{"key": "h2h", "outcomes": [
                        {"name": "Spain", "price": 1.30},
                        {"name": "Cape Verde", "price": 9.0},
                        {"name": "Draw", "price": 5.0},
                    ]}],
                }]},
            }],
            "credits_used": 11,
            "aborted":      False,
            "reason_codes": [],
        }
    # Aseguramos que la ruta openfootball del primer torneo NO existe.
    with patch.object(orch, "fetch_tournament_pit_odds", _fake_fetch), \
         patch.object(orch, "NATIONAL_TOURNAMENTS", [
             ("world_cup_2022", "soccer_fifa_world_cup",
              "2022-11-27T00:00:00Z", "2022-11-27T23:59:59Z",
              "/tmp/__does_not_exist_d7__.json"),
         ]):
        res = asyncio.run(orch.run_national_block(
            max_credits=200, min_edge_pp=4.0,
        ))
    entry = res["per_tournament"]["world_cup_2022"]
    assert entry["available"] is False
    assert entry["reason_code"] == "GROUND_TRUTH_MISSING"
    assert entry["events_fetched"] == 1
    assert entry["openfootball_present"] is False


def test_national_block_skip_consumes_no_credits():
    """``skip=True`` debe marcar todos los torneos como
    ``SKIPPED_BY_USER`` y NO llamar al cliente histórico."""
    called = {"n": 0}
    async def _spy_fetch(**kwargs):
        called["n"] += 1
        return {"available": False, "events": [], "credits_used": 0,
                "aborted": False, "reason_codes": []}
    with patch.object(orch, "fetch_tournament_pit_odds", _spy_fetch):
        res = asyncio.run(orch.run_national_block(
            max_credits=3000, min_edge_pp=4.0, skip=True,
        ))
    assert called["n"] == 0
    assert res["credits_used"] == 0
    for label, *_ in orch.NATIONAL_TOURNAMENTS:
        assert res["per_tournament"][label]["reason_code"] == \
                "SKIPPED_BY_USER"


# ════════════════════════════════════════════════════════════════════════
# 11) main_async respeta --skip-national y --min-edge-pp
# ════════════════════════════════════════════════════════════════════════
def test_main_async_skip_national_flag(tmp_path):
    """Al invocar ``main_async`` con ``skip_national=True``, el
    reporte resultante debe llevar ``skip_national: true`` y el
    bloque nacional con todos los torneos en ``SKIPPED_BY_USER``."""
    out = tmp_path / "report.json"
    # Stub del bloque doméstico para que no descargue CSV ni corra
    # backtest pesado.
    def _empty_domestic(**_kwargs):
        return {"block": "domestic_leagues_summary",
                 "odds_type": "OPENING",
                 "per_league": {},
                 "_picks": []}
    with patch.object(orch, "run_domestic_block", _empty_domestic):
        report = asyncio.run(orch.main_async(
            max_credits=3000, out_path=str(out),
            min_edge_pp=3.5, skip_national=True,
        ))
    assert report["skip_national"] is True
    assert report["min_edge_pp"] == 3.5
    assert report["credits_used"] == 0
    nat = report["national_tournaments_summary"]["per_tournament"]
    assert all(v["reason_code"] == "SKIPPED_BY_USER"
                 for v in nat.values())
    # El reporte debe persistirse a disco.
    persisted = json.loads(out.read_text())
    assert persisted["skip_national"] is True


# ════════════════════════════════════════════════════════════════════════
# 12) Sprint D7-E · EDGE_VALUE_THRESHOLD_PP es parametrizable end-to-end
# ════════════════════════════════════════════════════════════════════════
def test_compute_draw_potential_threshold_override_changes_label():
    """``compute_draw_potential`` debe respetar el override de
    ``value_threshold_pp``: con un edge de +5pp y el default (4.0) se
    devuelve VALUE_DRAW; con threshold=6.0 debe degradar a FAIR."""
    from services import football_draw_potential as fdp
    # Construir inputs que generen edge ≈ 5pp.
    # base 0.24 + balance perfecto + low_goal_env ≈ 0.27 → 27 vs implied 22.
    common = dict(
        elo_home=1500, elo_away=1500,
        xg_home_l5=1.2, xg_away_l5=1.2,
        low_goal_environment=True,
        market_implied_draw_prob=0.22,
    )
    v_default = fdp.compute_draw_potential(**common)
    v_strict  = fdp.compute_draw_potential(**common,
                                              value_threshold_pp=6.0)
    # Si el edge cae entre [4, 6), el label debe cambiar de VALUE_DRAW
    # a FAIR_DRAW al subir el umbral.
    if v_default["edge"] is not None and 4.0 <= v_default["edge"] < 6.0:
        assert v_default["label"] == fdp.LABEL_VALUE_DRAW
        assert v_strict["label"]  == fdp.LABEL_FAIR_DRAW
    # Auditoría debe registrar el threshold efectivo.
    assert v_strict["debug"]["value_threshold_pp_effective"] == 6.0
    assert v_default["debug"]["value_threshold_pp_effective"] == 4.0


def test_run_backtest_min_edge_pp_actually_changes_picks(tmp_path):
    """REGRESIÓN del bug D7-E: ``run_backtest(min_edge_pp=2.0)`` debe
    producir MÁS picks que ``min_edge_pp=4.0`` cuando hay partidos con
    edge en [2pp, 4pp). Antes del fix, el ``label`` del módulo
    Draw Potential rechazaba esos picks aguas arriba aunque el engine
    los hubiera querido aceptar."""
    from services.football_backtest_engine import run_backtest
    from services.football_historical_ingestor import parse_football_data_csv
    # Usar Premier 2024/25 (380 partidos reales en cache).
    csv_text = Path("/app/data/football_data_co_uk/E0_2425.csv").read_text()
    matches = parse_football_data_csv(csv_text, competition="premier_league")
    bt_strict = run_backtest(
        matches, market="DRAW", no_market=False,
        use_calibration=False, walk_forward=True,
        shrinkage_K=None, min_pred_prob_pp=8.0, min_edge_pp=4.0,
    )
    bt_loose = run_backtest(
        matches, market="DRAW", no_market=False,
        use_calibration=False, walk_forward=True,
        shrinkage_K=None, min_pred_prob_pp=8.0, min_edge_pp=2.0,
    )
    n_strict = len(bt_strict.get("picks", []))
    n_loose  = len(bt_loose.get("picks", []))
    assert n_loose > n_strict, (
        f"Bajar min_edge_pp de 4.0 a 2.0 debe producir más picks. "
        f"strict={n_strict}, loose={n_loose}. Si son iguales, el "
        f"threshold del label sigue hardcodeado aguas arriba.")


def test_default_threshold_preserves_legacy_behavior():
    """No regresión: ejecutar ``run_backtest`` con los defaults debe
    producir exactamente la misma cantidad de picks que la corrida
    histórica documentada (Premier 24/25 → 149 picks @ 4pp)."""
    from services.football_backtest_engine import run_backtest
    from services.football_historical_ingestor import parse_football_data_csv
    csv_text = Path("/app/data/football_data_co_uk/E0_2425.csv").read_text()
    matches = parse_football_data_csv(csv_text, competition="premier_league")
    bt = run_backtest(
        matches, market="DRAW", no_market=False,
        use_calibration=True, walk_forward=True,
        shrinkage_K=50, min_pred_prob_pp=8.0, min_edge_pp=4.0,
    )
    # Si este número cambia es porque algo del pipeline se movió y
    # debemos investigar. 149 es el valor publicado en
    # /app/backtest_d7_domestic_edge4.json.
    assert len(bt["picks"]) == 149

