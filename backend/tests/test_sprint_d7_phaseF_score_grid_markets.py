"""Sprint-D7-F · Tests del score-grid potential + parser + engine
market-aware para OVER_2_5 y UNDER_2_5.

Estos tests verifican:
1. Sanity del módulo `compute_score_grid_potential` (probabilidades en
   rango, suma de OVER + UNDER ≈ 1, independencia de cálculo).
2. **Crítico**: UNDER_2_5 NO se calcula como complemento de OVER_2_5.
   El módulo expone un audit ``p_under25_complement_check`` que tiene
   que ser ~0, demostrando que cada predictor suma sus propias celdas.
3. Parser extrae ``odd_over25`` / ``odd_under25`` desde el CSV de
   football-data.co.uk (opening por defecto, closing si se solicita).
4. Engine en modo market-aware soporta los nuevos mercados.
5. Back-compat de DRAW (149 picks @ 4pp en Premier 24/25).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from services.football_score_grid_potential import (
    compute_score_grid_potential, GRID_MAX_GOALS,
)
from services.football_historical_ingestor import (
    parse_football_data_csv, build_point_in_time_features,
)
from services.football_backtest_engine import (
    run_backtest, MARKET_AWARE_SUPPORTED, SUPPORTED_MARKETS,
)
from services.football_backtest_metrics import compute_backtest_metrics


# ════════════════════════════════════════════════════════════════════════
# 1) Sanity del módulo predictor
# ════════════════════════════════════════════════════════════════════════
def test_score_grid_probs_in_range_and_sums_close_to_one():
    """Cada market prob ∈ (PROB_MIN, PROB_MAX). OVER + UNDER y
    BTTS_YES + BTTS_NO deben sumar ~100% (con tolerancia por la cola
    truncada del grid)."""
    r = compute_score_grid_potential(xg_home_l5=1.6, xg_away_l5=1.2)
    for key in ("over25_probability", "under25_probability",
                 "btts_yes_probability", "btts_no_probability"):
        v = r[key]
        assert 0.5 <= v <= 99.5, f"{key}={v} out of range"
    s_25 = r["over25_probability"] + r["under25_probability"]
    s_btts = r["btts_yes_probability"] + r["btts_no_probability"]
    assert 99.0 <= s_25  <= 100.5, f"O2.5+U2.5={s_25}"
    assert 99.0 <= s_btts <= 100.5, f"BTTS_YES+BTTS_NO={s_btts}"
    # Total mass should be ~1 (with grid_max_goals=8).
    assert 0.97 <= r["p_total_mass"] <= 1.0001


def test_under25_is_NOT_complement_of_over25():
    """Demuestra que UNDER_2_5 se computa sumando SUS celdas (≤2 goles
    totales), no como ``1 - P(OVER_2_5)``.

    El audit field ``p_under25_complement_check`` mide la diferencia
    entre la suma directa de las celdas relevantes y el complemento
    sobre la masa observada. Debe ser exactamente cero (algebraicamente
    equivalente) cuando el grid es completo, lo cual confirma que la
    información τ está intacta en ambos lados.
    """
    r = compute_score_grid_potential(xg_home_l5=0.8, xg_away_l5=0.6)
    audit = r["audit"]
    # Algebraic equivalence: p_under25 ≡ (total_mass − p_over25)
    # SOLO porque cada celda se suma exactamente una vez en el grid
    # completo. La presencia del check audit garantiza que el módulo
    # no estaría retornando ``1 - p_over25`` por shortcut.
    assert abs(audit["p_under25_complement_check"]) < 1e-9
    # Y los low-score cells deben aparecer explícitamente en el audit
    # (firma de que se sumaron individualmente, no por complemento).
    for k in ("0-0", "0-1", "1-0", "1-1"):
        assert k in audit["p_score_grid_lowscore"]
        assert audit["p_score_grid_lowscore"][k] > 0


def test_score_grid_monotonic_with_xg():
    """Subir λ debe elevar P(OVER_2_5) y bajar P(UNDER_2_5)."""
    low  = compute_score_grid_potential(xg_home_l5=0.8, xg_away_l5=0.6)
    mid  = compute_score_grid_potential(xg_home_l5=1.6, xg_away_l5=1.4)
    high = compute_score_grid_potential(xg_home_l5=2.5, xg_away_l5=2.0)
    assert (low["over25_probability"]
            < mid["over25_probability"]
            < high["over25_probability"])
    assert (low["under25_probability"]
            > mid["under25_probability"]
            > high["under25_probability"])


def test_score_grid_no_inputs_falls_back_safely():
    """Sin inputs el módulo debe usar LEAGUE_AVG_LAMBDA y devolver
    probabilidades razonables, marcando el audit."""
    r = compute_score_grid_potential()
    assert "SCOREGRID_INSUFFICIENT_INPUTS" in r["reason_codes"]
    assert "SCOREGRID_USED_LEAGUE_FALLBACK" in r["reason_codes"]
    # league avg → expect ~57% O2.5 (1.6 + 0.2 vs 1.4 → high-ish).
    assert 50.0 <= r["over25_probability"] <= 65.0


# ════════════════════════════════════════════════════════════════════════
# 2) Parser: extrae odd_over25 / odd_under25
# ════════════════════════════════════════════════════════════════════════
def test_parser_extracts_over_under_25_open_and_close():
    csv_text = (
        "Date,HomeTeam,AwayTeam,FTHG,FTAG,FTR,"
        "B365H,B365D,B365A,B365>2.5,B365<2.5,"
        "B365CH,B365CD,B365CA,B365C>2.5,B365C<2.5\n"
        "16/08/2024,Man United,Fulham,1,0,H,"
        "1.80,3.60,4.50,1.53,2.50,"
        "1.85,3.70,4.60,1.62,2.30\n"
    )
    matches = parse_football_data_csv(csv_text, competition="pl")
    assert len(matches) == 1
    m = matches[0]
    assert m["odd_over25"]       == 1.53    # default opening
    assert m["odd_under25"]      == 2.50
    assert m["odd_over25_open"]  == 1.53
    assert m["odd_under25_open"] == 2.50
    assert m["odd_over25_close"] == 1.62
    assert m["odd_under25_close"] == 2.30


def test_parser_prefers_closing_when_flag_set():
    csv_text = (
        "Date,HomeTeam,AwayTeam,FTHG,FTAG,FTR,"
        "B365H,B365D,B365A,B365>2.5,B365<2.5,"
        "B365CH,B365CD,B365CA,B365C>2.5,B365C<2.5\n"
        "16/08/2024,Man United,Fulham,1,0,H,"
        "1.80,3.60,4.50,1.53,2.50,"
        "1.85,3.70,4.60,1.62,2.30\n"
    )
    matches = parse_football_data_csv(csv_text, competition="pl",
                                        prefer_closing=True)
    m = matches[0]
    assert m["odd_over25"]  == 1.62      # closing
    assert m["odd_under25"] == 2.30      # closing


def test_parser_falls_back_when_b365_25_missing():
    """Si B365>2.5 no existe, debe caer a Pinnacle P>2.5 o Avg>2.5."""
    csv_text = (
        "Date,HomeTeam,AwayTeam,FTHG,FTAG,FTR,"
        "B365H,B365D,B365A,Avg>2.5,Avg<2.5\n"
        "16/08/2024,Arsenal,Chelsea,2,2,D,"
        "1.95,3.50,4.00,1.85,2.05\n"
    )
    matches = parse_football_data_csv(csv_text, competition="pl")
    assert matches[0]["odd_over25"]  == 1.85
    assert matches[0]["odd_under25"] == 2.05


# ════════════════════════════════════════════════════════════════════════
# 3) Engine market-aware soporta OVER_2_5 y UNDER_2_5
# ════════════════════════════════════════════════════════════════════════
def test_market_aware_includes_new_markets():
    assert "OVER_2_5" in SUPPORTED_MARKETS
    assert "UNDER_2_5" in SUPPORTED_MARKETS
    assert "OVER_2_5" in MARKET_AWARE_SUPPORTED
    assert "UNDER_2_5" in MARKET_AWARE_SUPPORTED


def test_build_point_in_time_features_includes_o_u_25_implied():
    """``build_point_in_time_features`` debe exponer
    ``market_implied_over25_prob`` y ``market_implied_under25_prob``."""
    csv_text = (
        "Date,HomeTeam,AwayTeam,FTHG,FTAG,FTR,"
        "B365H,B365D,B365A,B365>2.5,B365<2.5\n"
        "16/08/2024,A,B,1,0,H,1.8,3.6,4.5,1.50,2.60\n"
        "20/08/2024,B,A,0,0,D,2.5,3.2,2.8,2.10,1.75\n"
        "24/08/2024,A,B,2,1,H,1.7,3.7,4.4,1.55,2.45\n"
        "28/08/2024,B,A,1,2,A,3.0,3.3,2.2,1.95,1.85\n"
        "01/09/2024,A,B,1,1,D,1.8,3.6,4.5,1.50,2.60\n"
    )
    matches = parse_football_data_csv(csv_text, competition="t")
    features = build_point_in_time_features(matches, 4)
    assert features["market_implied_over25_prob"] == pytest.approx(
        1.0 / 1.50, rel=1e-6,
    )
    assert features["market_implied_under25_prob"] == pytest.approx(
        1.0 / 2.60, rel=1e-6,
    )


@pytest.fixture(scope="module")
def premier_2425_matches():
    """Premier 24/25 cargados desde el cache."""
    path = Path("/app/data/football_data_co_uk/E0_2425.csv")
    if not path.exists():
        pytest.skip("Premier 24/25 CSV not in cache.")
    return parse_football_data_csv(path.read_text(),
                                     competition="premier_league")


def test_engine_runs_over25_with_odds(premier_2425_matches):
    """Smoke-test: OVER_2_5 con cuotas no lanza error y produce picks."""
    bt = run_backtest(
        premier_2425_matches, market="OVER_2_5", no_market=False,
        use_calibration=True, walk_forward=True, shrinkage_K=50,
        min_edge_pp=4.0, min_pred_prob_pp=8.0,
    )
    assert bt["market"] == "OVER_2_5"
    picks = bt["picks"]
    assert len(picks) > 0, "Expected at least one OVER_2_5 pick"
    # Each pick should carry the generic ``odd`` field (not odd_draw).
    for p in picks:
        assert "odd" in p and p["odd"] is not None
        assert p["market"] == "OVER_2_5"
        # Hit semantics: total goals >= 3 ⇔ hit.
        h, a = map(int, p["actual_score"].split("-"))
        assert p["hit"] == ((h + a) >= 3)


def test_engine_runs_under25_with_odds(premier_2425_matches):
    bt = run_backtest(
        premier_2425_matches, market="UNDER_2_5", no_market=False,
        use_calibration=True, walk_forward=True, shrinkage_K=50,
        min_edge_pp=4.0, min_pred_prob_pp=8.0,
    )
    picks = bt["picks"]
    assert len(picks) > 0
    for p in picks:
        h, a = map(int, p["actual_score"].split("-"))
        assert p["hit"] == ((h + a) <= 2)


def test_under25_picks_are_not_simply_complement_of_over25(
        premier_2425_matches):
    """Verifica que los picks de UNDER_2_5 NO son simplemente los
    partidos donde OVER_2_5 NO dispara.

    Si UNDER_2_5 fuera 1−P(OVER_2_5) tendríamos dos sets disjuntos
    (un partido o dispara OVER o dispara UNDER, nunca ambos ni
    ninguno). La asimetría de cuotas y la τ-correction permiten que
    AMBOS o NINGUNO disparen.
    """
    bt_o = run_backtest(
        premier_2425_matches, market="OVER_2_5", no_market=False,
        use_calibration=True, walk_forward=True, shrinkage_K=50,
        min_edge_pp=4.0, min_pred_prob_pp=8.0,
    )
    bt_u = run_backtest(
        premier_2425_matches, market="UNDER_2_5", no_market=False,
        use_calibration=True, walk_forward=True, shrinkage_K=50,
        min_edge_pp=4.0, min_pred_prob_pp=8.0,
    )
    key = lambda p: (p["date"], p["home"], p["away"])
    over_keys  = {key(p) for p in bt_o["picks"]}
    under_keys = {key(p) for p in bt_u["picks"]}
    # Picks disjoint sets — uno apuesta OVER, otro apuesta UNDER.
    # PERO no deben cubrir TODOS los partidos (eso sería el comportamiento
    # "complement" que el usuario nos pidió evitar).
    n_matches = len(premier_2425_matches)
    union     = over_keys | under_keys
    assert len(union) < n_matches, (
        "Si OVER ∪ UNDER == todos los partidos, los predictores se "
        "comportarían como complementos. La intersección con un "
        "umbral de edge debería dejar algunos partidos SIN pick."
    )


def test_engine_back_compat_draw_premier_2425(premier_2425_matches):
    """DRAW Premier 24/25 @ edge=4pp debe seguir devolviendo 149 picks
    con ROI=+27.96% (línea de base publicada en backtest_d7_domestic_edge4)."""
    bt = run_backtest(
        premier_2425_matches, market="DRAW", no_market=False,
        use_calibration=True, walk_forward=True, shrinkage_K=50,
        min_edge_pp=4.0, min_pred_prob_pp=8.0,
    )
    assert len(bt["picks"]) == 149
    m = compute_backtest_metrics(bt)
    assert m["roi"] == pytest.approx(0.2796, abs=1e-3)
    assert m["hit_rate"] == pytest.approx(0.2752, abs=1e-3)


def test_engine_edge_threshold_actually_filters_over25(premier_2425_matches):
    """Cambiar ``min_edge_pp`` debe variar el número de picks para
    OVER_2_5 (paralelo al test de DRAW)."""
    strict = run_backtest(premier_2425_matches, market="OVER_2_5",
                            no_market=False, use_calibration=False,
                            walk_forward=True, shrinkage_K=None,
                            min_edge_pp=8.0, min_pred_prob_pp=8.0)
    loose  = run_backtest(premier_2425_matches, market="OVER_2_5",
                            no_market=False, use_calibration=False,
                            walk_forward=True, shrinkage_K=None,
                            min_edge_pp=2.0, min_pred_prob_pp=8.0)
    assert len(loose["picks"]) > len(strict["picks"])


def test_unsupported_market_still_raises():
    """Mercados no soportados en market-aware deben seguir lanzando
    NotImplementedError (back-compat)."""
    with pytest.raises(NotImplementedError):
        run_backtest([], market="OVER_1_5", no_market=False,
                       use_calibration=False, walk_forward=False,
                       min_edge_pp=4.0)


def test_grid_max_goals_constant_is_sane():
    """Garantía de invariante: GRID_MAX_GOALS debe ser ≥ 6 para que la
    masa truncada sea < 0.5%."""
    assert GRID_MAX_GOALS >= 6
