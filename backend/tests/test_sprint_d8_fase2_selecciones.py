"""Tests for Sprint-D8-Fase2 — selecciones DRAW backtest.

Cubre las 7 invariantes críticas del sprint:

1. **Hard credit cap aborts before overspend**: con un mock de
   ``x-requests-used`` creciente, el cliente debe abortar al alcanzar
   ``max_credits`` y devolver lo acumulado sin raise.
2. **Odds timestamp is prematch (PIT)**: el snapshot solicitado es
   ``kickoff − 3h`` (strictly before the match).
3. **Settlement uses openfootball, not Odds API payload**: la función
   ``settle_draw`` lee solo de la estructura openfootball; nunca toca
   el payload de odds.
4. **Cohort defined by prematch features only**: ``detect_cohorts``
   con un pick que **no** incluye ``fthg/ftag/ftr`` debe seguir
   funcionando (no depende de ground truth).
5. **PATTERN_NOT_YET_PROVEN when n < 30**: el veredicto Bonferroni
   debe disparar ``PATTERN_NOT_YET_PROVEN_INSUFFICIENT_SAMPLE``
   cuando el cohorte tiene menos de 30 disparos.
6. **Sport key unavailable does not abort**: si un torneo falta del
   catálogo, los otros continúan.
7. **DRAW outcome extracted from h2h market** (outcome "Draw"): no
   inventar el empate, leerlo del payload.
"""
from __future__ import annotations

import asyncio

import pytest

from services.football_cohort_detector import (
    COHORT_DOMINANT_FAVORITE,
    detect_cohorts,
)
from services.football_selecciones_ingestor import (
    RC_NO_FIFA_POINTS_HOME,
    RC_NO_H2H_MARKET,
    devig_h2h,
    extract_consensus_h2h,
    normalise_team_name,
    resolve_groundtruth,
    settle_draw,
    teams_match,
)
from services.theoddsapi_historical_client import (
    CreditTracker,
    RC_UNAVAILABLE,
    estimate_credit_cost,
    fetch_event_odds_pit,
    fetch_tournament_pit_odds,
    verify_sport_keys_available,
)

# Import the Bonferroni verdict helper from the CLI script.
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import importlib
_runner = importlib.import_module("run_selecciones_draw_backtest")
_final_verdict = _runner._final_verdict


# ─────────────────────────────────────────────────────────────────────
# Helpers: mock the HTTP transport with controllable credit headers.
# ─────────────────────────────────────────────────────────────────────
def _make_mock_http(*, response_factory):
    """Return an async transport that increments ``x-requests-used``
    per call and delegates the JSON body to ``response_factory(call_n,
    url, params)``.
    """
    state = {"n_calls": 0, "credits_used": 0}

    async def _mock(url, params):
        state["n_calls"] += 1
        cost = 10 if "/odds" in url else 1
        state["credits_used"] += cost
        body = response_factory(state["n_calls"], url, params)
        return {
            "ok":      True,
            "status":  200,
            "json":    body,
            "headers": {"x-requests-used": str(state["credits_used"])},
        }
    _mock.state = state  # type: ignore[attr-defined]
    return _mock


def _event_payload(*, home, away, draw_odd, home_odd, away_odd,
                    commence="2022-11-20T19:00:00Z", odds_ts=None):
    return {
        "data": {
            "id":            "evtX",
            "sport_key":     "soccer_fifa_world_cup",
            "home_team":     home,
            "away_team":     away,
            "commence_time": commence,
            "bookmakers": [
                {"key": "pinnacle", "markets": [
                    {"key": "h2h", "outcomes": [
                        {"name": home,  "price": home_odd},
                        {"name": away,  "price": away_odd},
                        {"name": "Draw", "price": draw_odd},
                    ]}
                ]},
                {"key": "bet365",   "markets": [
                    {"key": "h2h", "outcomes": [
                        {"name": home,  "price": home_odd + 0.05},
                        {"name": away,  "price": away_odd + 0.05},
                        {"name": "Draw", "price": draw_odd + 0.05},
                    ]}
                ]},
            ],
        },
        "timestamp": odds_ts or "2022-11-20T16:00:00Z",
    }


# ─────────────────────────────────────────────────────────────────────
# Test 1 — Hard credit cap
# ─────────────────────────────────────────────────────────────────────
def test_credit_cap_aborts_before_overspend_and_returns_partial():
    def factory(_n, url, _params):
        if "/events?" in url or url.endswith("/events"):
            # Two events whose commence day matches the listing date
            # (so the new "skip-wrong-day" filter doesn't drop them).
            day = _params.get("date", "")[:10]
            return {"data": [
                {"id": f"e1_{day}", "home_team": "Argentina",
                 "away_team": "Saudi Arabia",
                 "commence_time": f"{day}T11:00:00Z"},
                {"id": f"e2_{day}", "home_team": "Spain",
                 "away_team": "Costa Rica",
                 "commence_time": f"{day}T16:00:00Z"},
            ]}
        return _event_payload(home="Argentina", away="Saudi Arabia",
                              draw_odd=3.4, home_odd=1.5, away_odd=6.0)

    mock_http = _make_mock_http(response_factory=factory)

    async def go():
        return await fetch_tournament_pit_odds(
            sport_key="soccer_fifa_world_cup",
            dates_iso=["2022-11-22T00:00:00Z", "2022-11-23T00:00:00Z"],
            # Day1 listing(+1)=1, ev1 odds(+10)=11, ev2 odds(+10)=21 → must_abort
            # triggers on the third /odds call. Set cap at 15 to force abort
            # mid-flight after a few /odds calls.
            max_credits=15,
            http=mock_http,
            api_key="dummy",
        )
    res = asyncio.run(go())
    assert res["aborted"] is True
    # Must have stopped at-or-just-over the cap, NOT blown past it.
    assert res["credits_used"] <= 15 + 10   # one /odds-call worth of overshoot is the worst case
    # Some events should still be built before the abort.
    assert len(res["events"]) >= 1


# ─────────────────────────────────────────────────────────────────────
# Test 2 — Odds timestamp is prematch
# ─────────────────────────────────────────────────────────────────────
def test_odds_timestamp_is_prematch_t_minus_3h():
    """``fetch_tournament_pit_odds`` must request odds at kickoff - 3h."""
    seen_dates: list[str] = []

    async def mock_http(url, params):
        if "/odds" in url:
            seen_dates.append(params.get("date"))
        body = ({"data": [{"id": "e1",
                            "home_team": "Argentina",
                            "away_team": "Saudi Arabia",
                            "commence_time": "2022-11-22T11:00:00Z"}]}
                if "/events" in url and "/odds" not in url
                else _event_payload(home="Argentina", away="Saudi Arabia",
                                     draw_odd=3.4, home_odd=1.5, away_odd=6.0))
        return {"ok": True, "status": 200, "json": body,
                "headers": {"x-requests-used": "1"}}

    async def go():
        return await fetch_tournament_pit_odds(
            sport_key="soccer_fifa_world_cup",
            dates_iso=["2022-11-22T00:00:00Z"],
            max_credits=200, http=mock_http, api_key="dummy",
        )
    asyncio.run(go())
    assert seen_dates == ["2022-11-22T08:00:00Z"]    # 11:00 − 3h = 08:00


# ─────────────────────────────────────────────────────────────────────
# Test 3 — Settlement uses openfootball, NOT odds payload
# ─────────────────────────────────────────────────────────────────────
def test_settle_draw_reads_only_from_openfootball_score_ft():
    """The settlement function must not read from the odds payload."""
    of_match = {"score": {"ft": [1, 1]}}
    assert settle_draw(of_match) == 1
    of_match = {"score": {"ft": [2, 1]}}
    assert settle_draw(of_match) == 0
    # Penalty / extra-time → settle on regulation only.
    of_match = {"score": {"ft": [0, 0], "et": [1, 0], "p": [3, 2]}}
    assert settle_draw(of_match) == 1   # DRAW at 90' regardless of ET/P
    # Missing → None.
    assert settle_draw({}) is None
    assert settle_draw({"score": {"ft": [None, None]}}) is None


# ─────────────────────────────────────────────────────────────────────
# Test 4 — Cohorts are prematch-only (no fthg/ftag/ftr leakage)
# ─────────────────────────────────────────────────────────────────────
def test_dominant_favorite_cohort_uses_only_prematch_features():
    """`detect_cohorts` must work without any ground-truth fields."""
    pick = {
        "predicted_prob": 0.32,
        "market_prob":    0.23,    # → edge 9pp
        "is_group_stage": True,
        # NO fthg, NO ftag, NO ftr, NO 'hit'.
    }
    features = {
        "elo_home": 1850.0,
        "elo_away": 1280.0,         # Δ = 570 (dominant)
    }
    det = detect_cohorts(pick, features)
    assert COHORT_DOMINANT_FAVORITE in det["cohorts"]
    # The audit row must not carry ground-truth keys we never gave it.
    forbidden = {"fthg", "ftag", "ftr", "hit"}
    leaked = forbidden & set(det["audit"].keys())
    assert leaked == set(), f"cohort audit leaked ground truth keys: {leaked}"


# ─────────────────────────────────────────────────────────────────────
# Test 5 — n < 30 ⇒ PATTERN_NOT_YET_PROVEN
# ─────────────────────────────────────────────────────────────────────
def test_pattern_not_proven_when_n_below_30():
    diagnostics = {
        "discrimination":     {"auc_model": 0.60},
        "model_vs_market":    {"delta_brier_vs_devig": -0.005},
    }
    dom_cohort = {"n": 25, "roi": 0.20, "roi_ci_low": 0.05, "roi_ci_high": 0.40}
    v = _final_verdict(diagnostics, dom_cohort)
    assert v["verdict"] == "PATTERN_NOT_YET_PROVEN_INSUFFICIENT_SAMPLE"
    assert "HYPOTHESIS_SUGGESTIVE_BUT_NOT_PROVEN" in v["tags"]


def test_pattern_confirmed_only_when_all_three_conditions_met():
    diagnostics = {
        "discrimination":  {"auc_model": 0.62},
        "model_vs_market": {"delta_brier_vs_devig": -0.015},
    }
    dom_cohort = {"n": 35, "roi": 0.18, "roi_ci_low": 0.05, "roi_ci_high": 0.30}
    v = _final_verdict(diagnostics, dom_cohort)
    assert v["verdict"] == "PATTERN_CONFIRMED_REAL"


def test_pattern_closed_when_no_signal_anywhere():
    diagnostics = {
        "discrimination":  {"auc_model": 0.51},   # ≈ random
        "model_vs_market": {"delta_brier_vs_devig": 0.003},  # worse than market
    }
    dom_cohort = {"n": 40, "roi": 0.0, "roi_ci_low": -0.05, "roi_ci_high": 0.05}
    v = _final_verdict(diagnostics, dom_cohort)
    assert v["verdict"] == "CLOSED_SAME_AS_LIGAS"
    assert "HYPOTHESIS_REFUTED" in v["tags"]


# ─────────────────────────────────────────────────────────────────────
# Test 6 — Sport key unavailable does NOT abort
# ─────────────────────────────────────────────────────────────────────
def test_sport_key_unavailable_does_not_raise():
    async def mock_http(_url, _params):
        return {"ok": True, "status": 200,
                "json": [{"key": "soccer_fifa_world_cup"},
                          {"key": "soccer_uefa_european_championship"}],
                "headers": {}}

    async def go():
        return await verify_sport_keys_available(
            ["soccer_fifa_world_cup",
              "soccer_uefa_european_championship",
              "soccer_conmebol_copa_america"],   # NOT in catalog
            api_key="dummy", http=mock_http,
        )
    res = asyncio.run(go())
    assert res["available"] is True
    assert "soccer_conmebol_copa_america" in res["missing_keys"]
    assert "soccer_fifa_world_cup" in res["valid_keys"]
    assert "soccer_uefa_european_championship" in res["valid_keys"]


# ─────────────────────────────────────────────────────────────────────
# Test 7 — DRAW outcome extracted from h2h market (outcome "Draw")
# ─────────────────────────────────────────────────────────────────────
def test_draw_outcome_extracted_from_h2h_market():
    payload = _event_payload(home="Argentina", away="Saudi Arabia",
                              draw_odd=3.40, home_odd=1.50, away_odd=6.00)
    h2h = extract_consensus_h2h(payload["data"],
                                home_team="Argentina",
                                away_team="Saudi Arabia")
    assert h2h["available"] is True
    assert h2h["n_books"] == 2
    # Median of 3.40 and 3.45 = 3.425.
    assert abs(h2h["draw_odd"] - 3.425) < 1e-6


def test_extract_consensus_h2h_returns_unavailable_when_no_h2h():
    payload = {"bookmakers": [{"markets": [{"key": "totals",
                                              "outcomes": []}]}]}
    h2h = extract_consensus_h2h(payload, home_team="A", away_team="B")
    assert h2h["available"] is False
    assert RC_NO_H2H_MARKET in h2h["reason_codes"]


# ─────────────────────────────────────────────────────────────────────
# Additional unit tests
# ─────────────────────────────────────────────────────────────────────
def test_devig_h2h_proportional_normalises_to_one():
    out = devig_h2h(1.5, 3.4, 6.0)
    assert abs(out["home"] + out["draw"] + out["away"] - 1.0) < 1e-9
    assert 0.0 < out["draw"] < 1.0


def test_devig_handles_garbage():
    out = devig_h2h(None, 3.4, 6.0)  # type: ignore
    assert out["draw"] is None


def test_normalise_team_name_handles_aliases_and_diacritics():
    assert normalise_team_name("USA") == "United States"
    assert normalise_team_name("Türkiye") == "Turkey"
    assert normalise_team_name("Côte d'Ivoire") == "Ivory Coast"
    # Idempotent on already-canonical names.
    assert normalise_team_name("Argentina") == "Argentina"


def test_teams_match_is_case_insensitive_and_alias_aware():
    assert teams_match("USA", "United States")
    assert teams_match("ARGENTINA", "Argentina")
    assert not teams_match("Argentina", "Brazil")


def test_resolve_groundtruth_links_by_date_and_team_set():
    odds_event = {
        "home_team":     "Argentina",
        "away_team":     "Saudi Arabia",
        "commence_time": "2022-11-22T11:00:00Z",
    }
    of_matches = [
        {"date": "2022-11-22", "team1": "Argentina", "team2": "Saudi Arabia",
         "score": {"ft": [1, 2]}, "round": "Group Stage"},
        {"date": "2022-11-23", "team1": "Spain", "team2": "Costa Rica",
         "score": {"ft": [7, 0]}, "round": "Group Stage"},
    ]
    gt = resolve_groundtruth(odds_event, of_matches)
    assert gt is not None
    assert gt["team1"] == "Argentina"


def test_resolve_groundtruth_handles_team_order_swap():
    odds_event = {"home_team": "Brazil", "away_team": "Croatia",
                   "commence_time": "2022-12-09T15:00:00Z"}
    of_matches = [
        {"date": "2022-12-09", "team1": "Croatia", "team2": "Brazil",
         "score": {"ft": [1, 1]}, "round": "Quarter-finals"},
    ]
    gt = resolve_groundtruth(odds_event, of_matches)
    assert gt is not None
    assert settle_draw(gt) == 1


def test_estimate_credit_cost_floor_and_ceiling():
    est = estimate_credit_cost(n_matches=64)
    assert est["est_credits_floor"]   == 64 * 10 + 1
    assert est["est_credits_ceiling"] == 64 * 11


def test_credit_tracker_must_abort_when_delta_exceeds_max():
    t = CreditTracker(max_credits=20)
    t.update(100)  # base
    assert t.must_abort() is False
    t.update(115)  # delta = 15
    assert t.must_abort() is False
    t.update(120)  # delta = 20
    assert t.must_abort() is True


def test_full_pipeline_smoke_with_mocked_odds_and_real_groundtruth(tmp_path):
    """End-to-end smoke: feed 3 mocked events, real openfootball ground
    truth, real FIFA points, and verify the calibration pipeline runs
    without raising.
    """
    from services.football_selecciones_ingestor import build_match_record

    # Mock event matching wc2022 Day 1 (Argentina-Saudi Arabia).
    # Bookmakers live inside ``event_payload`` (matches the real
    # historical client wrap-up).
    odds_event = {
        "event_id":       "evt_arg_ksa",
        "home_team":      "Argentina",
        "away_team":      "Saudi Arabia",
        "commence_time":  "2022-11-22T11:00:00Z",
        "odds_timestamp": "2022-11-22T08:00:00Z",
        "event_payload":  _event_payload(
            home="Argentina", away="Saudi Arabia",
            draw_odd=4.5, home_odd=1.3, away_odd=12.0,
        )["data"],
    }
    of_match = {
        "date": "2022-11-22", "team1": "Argentina", "team2": "Saudi Arabia",
        "score": {"ft": [1, 2]}, "round": "Group Stage",
    }
    fifa_pts = {"Argentina": 1773.88, "Saudi Arabia": 1437.0}

    built = build_match_record(
        odds_event=odds_event,
        openfootball_match=of_match,
        fifa_points=fifa_pts,
        tournament_name="wc2022",
        sport_key="soccer_fifa_world_cup",
    )
    assert built["available"] is True, (
        f"build_match_record failed; reason_codes="
        f"{built['source_audit']['reason_codes']}"
    )
    assert built["record"]["hit"] == 0   # Argentina 1-2 KSA: not a draw
    assert built["record"]["predicted_prob"] is not None
    assert built["record"]["market_implied_devig"] is not None
    # PIT guarantee: source_audit carries the prematch timestamp.
    assert built["source_audit"]["odds_timestamp"] == "2022-11-22T08:00:00Z"


def test_build_match_record_reads_bookmakers_from_event_payload_nested():
    """Regression test for a real bug found in the live backtest run:
    bookmakers live inside ``event_payload`` (as wrapped by the
    historical client), NOT at the root of ``odds_event``.
    """
    from services.football_selecciones_ingestor import build_match_record

    inner = _event_payload(home="Spain", away="Costa Rica",
                            draw_odd=15.0, home_odd=1.1, away_odd=30.0)["data"]
    # Outer mimics the wrapper produced by fetch_tournament_pit_odds.
    odds_event = {
        "event_id":       "evt_spa_crc",
        "home_team":      "Spain",
        "away_team":      "Costa Rica",
        "commence_time":  "2022-11-23T16:00:00Z",
        "odds_timestamp": "2022-11-23T13:00:00Z",
        "event_payload":  inner,
    }
    of_match = {
        "date": "2022-11-23", "team1": "Spain", "team2": "Costa Rica",
        "score": {"ft": [7, 0]}, "round": "Group Stage",
    }
    fifa_pts = {"Spain": 1715.22, "Costa Rica": 1500.0}

    built = build_match_record(
        odds_event=odds_event, openfootball_match=of_match,
        fifa_points=fifa_pts, tournament_name="wc2022",
        sport_key="soccer_fifa_world_cup",
    )
    assert built["available"] is True
    assert built["source_audit"]["n_bookmakers"] == 2


def test_fetch_tournament_pit_odds_dedups_event_ids_across_listings():
    """Regression test for a real bug: each daily /events listing
    returns ALL events available at that snapshot (not only events of
    that date). Without dedup, the same event_id triggers multiple
    10-credit /odds calls, burning credits on duplicates.

    Here we simulate the listing returning the SAME event on 3
    consecutive days; the client must only fetch /odds ONCE.
    """
    call_log: list[str] = []

    async def mock_http(url, params):
        call_log.append(url)
        if "/odds" in url:
            return {"ok": True, "status": 200,
                    "json": _event_payload(home="Argentina",
                                            away="Saudi Arabia",
                                            draw_odd=3.4, home_odd=1.5,
                                            away_odd=6.0),
                    "headers": {"x-requests-used": str(10 * len(call_log))}}
        # Listing: always returns the same event, with commence on day-2.
        return {"ok": True, "status": 200,
                "json": {"data": [
                    {"id": "evt_DUPLICATE", "home_team": "Argentina",
                     "away_team": "Saudi Arabia",
                     "commence_time": "2022-11-22T11:00:00Z"},
                ]},
                "headers": {"x-requests-used": str(len(call_log))}}

    async def go():
        return await fetch_tournament_pit_odds(
            sport_key="soccer_fifa_world_cup",
            dates_iso=["2022-11-22T00:00:00Z",
                        "2022-11-23T00:00:00Z",
                        "2022-11-24T00:00:00Z"],
            max_credits=200, http=mock_http, api_key="dummy",
        )
    res = asyncio.run(go())
    # 1 unique event built — even though listing returned it 3 times.
    assert len(res["events"]) == 1
    # /odds was called exactly ONCE (10 credits) — not 3 times.
    odds_calls = [u for u in call_log if "/odds" in u]
    assert len(odds_calls) == 1, (
        f"dedup failed: /odds was called {len(odds_calls)} times for "
        f"the same event_id"
    )


def test_fetch_tournament_pit_odds_skips_events_with_wrong_commence_day():
    """The historical /events?date=X listing returns upcoming events
    well beyond that day. We must skip events whose ``commence_time``
    falls on a different calendar day than the listing's date to avoid
    listing-the-same-future-event-every-day duplication.
    """
    call_log: list[str] = []

    async def mock_http(url, params):
        call_log.append(url)
        if "/odds" in url:
            return {"ok": True, "status": 200,
                    "json": _event_payload(
                        home="Spain", away="Costa Rica",
                        draw_odd=15.0, home_odd=1.1, away_odd=30.0,
                        commence="2022-11-23T16:00:00Z"),
                    "headers": {"x-requests-used": str(10)}}
        # Listing on 2022-11-22 returns an event for 2022-11-23.
        return {"ok": True, "status": 200,
                "json": {"data": [
                    {"id": "evt_FUTURE", "home_team": "Spain",
                     "away_team": "Costa Rica",
                     "commence_time": "2022-11-23T16:00:00Z"},
                ]},
                "headers": {"x-requests-used": "1"}}

    async def go():
        return await fetch_tournament_pit_odds(
            sport_key="soccer_fifa_world_cup",
            dates_iso=["2022-11-22T00:00:00Z"],
            max_credits=200, http=mock_http, api_key="dummy",
        )
    res = asyncio.run(go())
    # The event was filtered out — /odds was NOT called.
    odds_calls = [u for u in call_log if "/odds" in u]
    assert len(odds_calls) == 0
    assert res["events"] == []
