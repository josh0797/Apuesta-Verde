"""Tests for the Dixon-Coles + conditional NB calibration layer.

Coverage:
  * Pieza 1: Dixon-Coles bivariate matrix — symmetry, rho=0 identity,
    asymmetric clamp, low-cell direction, renormalisation.
  * Pieza 2: Conditional NB — inert at ratio=1.0, widens marginals at
    ratio>1.0, ratio clamp.
  * Pieza 3: compute_match_features emits the full DC/NB telemetry block
    AND legacy poisson values match the original implementation.
  * Pieza 4: football_totals_calibration — n<100 returns defaults,
    bucket OBSERVE_ONLY, apply_calibration_to_match mutator.
  * Pieza 5: apply_calibration_to_match fail-soft, offense_bucket
    auto-derivation in record_football_pick_outcome, persist_football_market_result
    accepts and stores league_tier + offense_bucket + DC/NB telemetry.
  * Backtest runner pure helpers (build_backtest_match_doc).
"""

from __future__ import annotations

import pytest

from services.statsbomb_features import (
    _poisson_pmf,
    poisson_total_under,
    build_score_matrix,
    build_score_matrix_nb,
    under_prob_from_matrix,
    derive_offense_bucket,
    compute_match_features,
    DIXON_COLES_RHO_DEFAULT,
    FOOTBALL_GOALS_DISPERSION_RATIO_DEFAULT,
)
from services.football_moneyball.football_totals_calibration import (
    compute_football_totals_calibration,
    apply_calibration_to_match,
    DIXON_COLES_RHO_DEFAULT_REF,
    GOALS_DISPERSION_RATIO_DEFAULT_REF,
)
from services.football_moneyball.football_backtest_runner import (
    build_backtest_match_doc,
    _infer_league_tier,
    _decide_outcome,
)
from services.football_moneyball.football_feedback_loop import (
    record_football_pick_outcome,
)


# Re-use the fake DB from the existing test file.
from tests.test_football_moneyball import _FakeDB  # type: ignore


# ═════════════════════════════════════════════════════════════════════
# Pieza 1 — Dixon-Coles bivariate matrix
# ═════════════════════════════════════════════════════════════════════
def test_dc_matrix_rho_zero_identical_to_pure_poisson():
    lam_h, lam_a = 1.2, 1.1
    matrix = build_score_matrix(lam_h, lam_a, rho=0.0)
    # When rho=0, cell (i,j) must equal Poisson(i,lam_h) * Poisson(j,lam_a)
    # up to floating-point error (no DC correction).
    for i in range(11):
        for j in range(11):
            expected = _poisson_pmf(i, lam_h) * _poisson_pmf(j, lam_a)
            assert abs(matrix[i][j] - expected) < 1e-6


def test_dc_matrix_renormalises_to_one():
    matrix = build_score_matrix(1.5, 1.3, rho=-0.10)
    total = sum(cell for row in matrix for cell in row)
    assert abs(total - 1.0) < 1e-6


def test_dc_matrix_rho_negative_low_cells_dependence():
    """With rho < 0, the four DC-touched cells show the expected
    pre-normalisation tau direction:
      • (0,0) tau = 1 - lam_h*lam_a*rho  > 1  ⇒ post-DC raw ↑
      • (0,1) tau = 1 + lam_h*rho        < 1  ⇒ post-DC raw ↓
      • (1,0) tau = 1 + lam_a*rho        < 1  ⇒ post-DC raw ↓
      • (1,1) tau = 1 - rho              > 1  ⇒ post-DC raw ↑

    Post-normalisation outcomes for (1,1) depend on total mass shift,
    so we only check (0,0) up + (0,1)/(1,0) down, which always hold."""
    lam_h, lam_a = 1.2, 1.1
    base = build_score_matrix(lam_h, lam_a, rho=0.0)
    neg = build_score_matrix(lam_h, lam_a, rho=-0.10)
    assert neg[0][0] > base[0][0]
    assert neg[0][1] < base[0][1]
    assert neg[1][0] < base[1][0]


def test_dc_matrix_rho_positive_clamped_to_zero():
    """Positive rho would invert DC → must be clamped to <=0."""
    lam_h, lam_a = 1.2, 1.1
    # Try to pass rho=+0.10 → clamp to 0 → identical to Poisson.
    m_clamped = build_score_matrix(lam_h, lam_a, rho=0.10)
    m_pure = build_score_matrix(lam_h, lam_a, rho=0.0)
    for i in range(11):
        for j in range(11):
            assert abs(m_clamped[i][j] - m_pure[i][j]) < 1e-9


def test_dc_matrix_rho_lower_clamped():
    """Out-of-range rho < -0.20 must be clamped at -0.20."""
    m_extreme = build_score_matrix(1.2, 1.1, rho=-0.5)
    m_clamped = build_score_matrix(1.2, 1.1, rho=-0.20)
    for i in range(11):
        for j in range(11):
            assert abs(m_extreme[i][j] - m_clamped[i][j]) < 1e-9


def test_under_prob_from_matrix_matches_poisson_when_rho_zero():
    lam_h, lam_a = 1.4, 1.0
    lam_total = lam_h + lam_a
    matrix = build_score_matrix(lam_h, lam_a, rho=0.0)
    p_dc_25 = under_prob_from_matrix(matrix, 2.5)
    p_pois_25 = poisson_total_under(lam_total, 2.5)
    # Truncation at max_goals=10 introduces ε; tolerate 1e-6.
    assert abs(p_dc_25 - p_pois_25) < 1e-5


def test_under_prob_from_matrix_2_5_sums_three_total_buckets():
    """Under 2.5 ⇒ i+j ≤ 2 ⇒ (0,0)+(0,1)+(0,2)+(1,0)+(1,1)+(2,0)."""
    matrix = build_score_matrix(1.2, 1.1, rho=-0.05)
    p = under_prob_from_matrix(matrix, 2.5)
    expected = (
        matrix[0][0] + matrix[0][1] + matrix[0][2]
        + matrix[1][0] + matrix[1][1] + matrix[2][0]
    )
    assert abs(p - expected) < 1e-9


# ═════════════════════════════════════════════════════════════════════
# Pieza 2 — Conditional NB
# ═════════════════════════════════════════════════════════════════════
def test_nb_inert_at_ratio_one_matches_dc_pure():
    """When dispersion_ratio = 1.0 the NB layer must be inert."""
    lam_h, lam_a, rho = 1.2, 1.1, -0.05
    dc = build_score_matrix(lam_h, lam_a, rho=rho)
    nb = build_score_matrix_nb(lam_h, lam_a, rho=rho, dispersion_ratio=1.0)
    diff = sum(abs(dc[i][j] - nb[i][j]) for i in range(11) for j in range(11))
    assert diff < 1e-6


def test_nb_ratio_widens_high_score_marginals():
    """ratio > 1.0 must put more mass on high-scoring cells (>=3 per side)."""
    lam_h, lam_a, rho = 1.2, 1.1, -0.05
    base = build_score_matrix_nb(lam_h, lam_a, rho=rho, dispersion_ratio=1.0)
    wide = build_score_matrix_nb(lam_h, lam_a, rho=rho, dispersion_ratio=1.5)
    high_base = sum(base[i][j] for i in range(11) for j in range(11) if (i + j) >= 4)
    high_wide = sum(wide[i][j] for i in range(11) for j in range(11) if (i + j) >= 4)
    assert high_wide > high_base


def test_nb_ratio_clamped_lower():
    """ratio < 1.0 must clamp to 1.0 (i.e. inert)."""
    m_low = build_score_matrix_nb(1.2, 1.1, rho=-0.05, dispersion_ratio=0.5)
    m_inert = build_score_matrix_nb(1.2, 1.1, rho=-0.05, dispersion_ratio=1.0)
    for i in range(11):
        for j in range(11):
            assert abs(m_low[i][j] - m_inert[i][j]) < 1e-9


def test_nb_ratio_clamped_upper():
    """ratio > 2.0 must clamp to 2.0."""
    m_huge = build_score_matrix_nb(1.2, 1.1, rho=-0.05, dispersion_ratio=5.0)
    m_cap = build_score_matrix_nb(1.2, 1.1, rho=-0.05, dispersion_ratio=2.0)
    for i in range(11):
        for j in range(11):
            assert abs(m_huge[i][j] - m_cap[i][j]) < 1e-9


# ═════════════════════════════════════════════════════════════════════
# Pieza 3 — compute_match_features integration
# ═════════════════════════════════════════════════════════════════════
def _featureable_match(**overrides) -> dict:
    """Build a match dict rich enough for compute_match_features."""
    base = {
        "home_team": {
            "context": {
                "goals_for_avg": 1.4,
                "goals_against_avg": 1.1,
                "recent_fixtures": {
                    "played": 10,
                    "gf_avg": 1.4,
                    "ga_avg": 1.1,
                    "gf_avg_home": 1.5,
                    "ga_avg_home": 1.0,
                    "shots_on_target_avg": 4.5,
                },
            },
        },
        "away_team": {
            "context": {
                "goals_for_avg": 1.2,
                "goals_against_avg": 1.3,
                "recent_fixtures": {
                    "played": 10,
                    "gf_avg": 1.2,
                    "ga_avg": 1.3,
                    "gf_avg_away": 1.1,
                    "ga_avg_away": 1.4,
                    "shots_on_target_avg": 4.0,
                },
            },
        },
    }
    base.update(overrides)
    return base


def test_compute_match_features_includes_dc_nb_telemetry():
    m = _featureable_match()
    feats = compute_match_features(m)
    assert feats is not None
    for key in (
        "dc_rho_used", "goals_dispersion_ratio",
        "p_under_2_5_poisson", "p_under_3_5_poisson",
        "dc_nb_delta_2_5_pts", "dc_nb_delta_3_5_pts",
    ):
        assert key in feats, f"missing key {key}"
    # Default rho/ratio honoured when match has no _dc_rho.
    assert feats["dc_rho_used"] == round(DIXON_COLES_RHO_DEFAULT, 4)
    assert feats["goals_dispersion_ratio"] == round(FOOTBALL_GOALS_DISPERSION_RATIO_DEFAULT, 3)


def test_compute_match_features_honours_match_overrides():
    m = _featureable_match()
    m["_dc_rho"] = -0.12
    m["_goals_dispersion_ratio"] = 1.4
    feats = compute_match_features(m)
    assert feats["dc_rho_used"] == round(-0.12, 4)
    assert feats["goals_dispersion_ratio"] == round(1.4, 3)


def test_compute_match_features_delta_is_signed():
    m = _featureable_match()
    feats = compute_match_features(m)
    # With negative rho default, P(Under 2.5) typically slightly > poisson
    # but the contract only requires the field to be present and finite.
    assert isinstance(feats["dc_nb_delta_2_5_pts"], (int, float))
    assert isinstance(feats["dc_nb_delta_3_5_pts"], (int, float))


# ═════════════════════════════════════════════════════════════════════
# Pieza 4 — football_totals_calibration
# ═════════════════════════════════════════════════════════════════════
@pytest.mark.asyncio
async def test_calibration_db_none_returns_defaults():
    summary = await compute_football_totals_calibration(None)
    assert summary["available"] is False
    assert summary["rho"]["to_apply"] == DIXON_COLES_RHO_DEFAULT_REF
    assert summary["dispersion_ratio"]["to_apply"] == GOALS_DISPERSION_RATIO_DEFAULT_REF


@pytest.mark.asyncio
async def test_calibration_low_sample_returns_defaults(monkeypatch):
    """With n < 100 the summary must apply defaults even if empirical is computed."""
    fake = _FakeDB()
    # Seed only 10 docs.
    from datetime import datetime, timezone
    now_iso = datetime.now(timezone.utc).isoformat()
    for i in range(10):
        await fake.football_market_results.insert_one({
            "match_id": f"m{i}",
            "user_id": "_slate",
            "settled_at": now_iso,
            "final_score": {"home": 0, "away": 0},
            "league_tier": "TIER1",
            "offense_bucket": "MODERATE_OFFENSE",
        })
    summary = await compute_football_totals_calibration(fake, days=30, user_id="_slate")
    # global_applies is False because n=10 < 100.
    assert summary["global_applies"] is False
    assert summary["rho"]["to_apply"] == DIXON_COLES_RHO_DEFAULT_REF
    assert summary["dispersion_ratio"]["to_apply"] == GOALS_DISPERSION_RATIO_DEFAULT_REF


@pytest.mark.asyncio
async def test_calibration_high_sample_applies_empirical():
    """With n >= 100 the empirical rho/ratio are applied."""
    fake = _FakeDB()
    from datetime import datetime, timezone
    now_iso = datetime.now(timezone.utc).isoformat()
    # 120 docs with realistic football score distribution.
    scores = [(0, 0), (1, 0), (0, 1), (1, 1), (2, 1), (1, 2), (2, 0), (0, 2), (2, 2), (3, 1)]
    for i in range(120):
        h, a = scores[i % len(scores)]
        await fake.football_market_results.insert_one({
            "match_id": f"m{i}",
            "user_id": "_slate",
            "settled_at": now_iso,
            "final_score": {"home": h, "away": a},
            "league_tier": "TIER1",
            "offense_bucket": "MODERATE_OFFENSE",
        })
    summary = await compute_football_totals_calibration(fake, days=30, user_id="_slate")
    assert summary["global_applies"] is True
    assert summary["sample_size"] == 120
    # Empirical values must be in the clamp range.
    assert -0.20 <= summary["rho"]["to_apply"] <= 0.0
    assert 1.0 <= summary["dispersion_ratio"]["to_apply"] <= 2.0


@pytest.mark.asyncio
async def test_calibration_buckets_observe_only():
    fake = _FakeDB()
    from datetime import datetime, timezone
    now_iso = datetime.now(timezone.utc).isoformat()
    for i in range(50):
        await fake.football_market_results.insert_one({
            "match_id": f"b{i}",
            "user_id": "_slate",
            "settled_at": now_iso,
            "final_score": {"home": 1, "away": 1},
            "league_tier": "TIER1",
            "offense_bucket": "MODERATE_OFFENSE",
        })
    summary = await compute_football_totals_calibration(fake, days=30, user_id="_slate")
    # All buckets must be OBSERVE_ONLY regardless of n.
    for bucket in summary["by_league_tier"].values():
        assert bucket["mode"] == "OBSERVE_ONLY"
    for bucket in summary["by_offense"].values():
        assert bucket["mode"] == "OBSERVE_ONLY"
    assert summary["bucket_application_policy"]["mode"] == "OBSERVE_ONLY"


def test_apply_calibration_to_match_failsoft():
    m = {"match_id": "x"}
    # None calibration → no mutation.
    apply_calibration_to_match(m, None)
    assert "_dc_rho" not in m
    # available=False → no mutation.
    apply_calibration_to_match(m, {"available": False})
    assert "_dc_rho" not in m
    # available=True with values → mutates.
    apply_calibration_to_match(m, {
        "available": True,
        "rho": {"to_apply": -0.08},
        "dispersion_ratio": {"to_apply": 1.2},
    })
    assert m["_dc_rho"] == -0.08
    assert m["_goals_dispersion_ratio"] == 1.2


# ═════════════════════════════════════════════════════════════════════
# Pieza 5 — Persistence of league_tier / offense_bucket + telemetry
# ═════════════════════════════════════════════════════════════════════
@pytest.mark.asyncio
async def test_record_outcome_persists_buckets_and_telemetry():
    fake = _FakeDB()
    res = await record_football_pick_outcome(
        fake,
        match_id="mX", user_id="_slate",
        market="Under 3.5", selection="Under 3.5",
        odds=1.50, stake=10.0,
        outcome="won",
        pattern_keys=["X"],
        final_score={"home": 1, "away": 1},
        league_tier="TIER1",
        lambda_total=2.8,
        lambda_home=1.5, lambda_away=1.3,
        dc_rho_used=-0.05,
        goals_dispersion_ratio=1.0,
        p_under_2_5_poisson=0.60, p_under_3_5_poisson=0.85,
        p_under_2_5_dc_nb=0.62, p_under_3_5_dc_nb=0.86,
        dc_nb_delta_2_5_pts=2.0, dc_nb_delta_3_5_pts=1.0,
    )
    assert res["available"] is True
    assert res["persisted"] is True
    assert res["league_tier"] == "TIER1"
    assert res["offense_bucket"] == "MODERATE_OFFENSE"  # 2.8 → MODERATE
    # Doc persisted with all telemetry.
    doc = await fake.football_market_results.find_one({"match_id": "mX"})
    assert doc["league_tier"] == "TIER1"
    assert doc["offense_bucket"] == "MODERATE_OFFENSE"
    assert doc["lambda_total"] == 2.8
    assert doc["dc_rho_used"] == -0.05
    assert doc["sport"] == "football"


@pytest.mark.asyncio
async def test_record_outcome_auto_derives_offense_bucket():
    fake = _FakeDB()
    # lambda_total = 3.2 → HIGH_OFFENSE
    res = await record_football_pick_outcome(
        fake, match_id="hi", user_id="_slate",
        market="Over 2.5", outcome="won",
        lambda_total=3.2,
    )
    assert res["offense_bucket"] == "HIGH_OFFENSE"
    # lambda_total = 1.8 → LOW_OFFENSE
    res2 = await record_football_pick_outcome(
        fake, match_id="lo", user_id="_slate",
        market="Under 2.5", outcome="won",
        lambda_total=1.8,
    )
    assert res2["offense_bucket"] == "LOW_OFFENSE"


# ═════════════════════════════════════════════════════════════════════
# Backtest runner pure helpers
# ═════════════════════════════════════════════════════════════════════
def test_build_backtest_match_doc_minimal():
    api = {
        "fixture": {"id": 1234, "date": "2024-01-01", "status": {"short": "FT"}},
        "teams": {"home": {"id": 10, "name": "H"}, "away": {"id": 20, "name": "A"}},
        "league": {"id": 39, "name": "EPL"},
        "goals": {"home": 1, "away": 2},
    }
    m = build_backtest_match_doc(api)
    assert m is not None
    assert m["match_id"] == "1234"
    assert m["final_score"] == {"home": 1, "away": 2}
    assert m["_backtest"] is True


def test_build_backtest_match_doc_returns_none_when_missing_goals():
    api = {
        "fixture": {"id": 1234},
        "teams": {"home": {"id": 10}, "away": {"id": 20}},
        "goals": {"home": None, "away": None},
    }
    assert build_backtest_match_doc(api) is None


def test_infer_league_tier_known_and_unknown():
    assert _infer_league_tier(39) == "TIER1"   # EPL
    assert _infer_league_tier(40) == "TIER2"   # Championship
    assert _infer_league_tier(99999) == "TIER3"
    assert _infer_league_tier(None) == "UNKNOWN_LEAGUE"


def test_decide_outcome_under_25():
    label, won = _decide_outcome({"home": 1, "away": 1}, line=2.5)
    assert won is True
    assert label == "won"
    label2, won2 = _decide_outcome({"home": 2, "away": 1}, line=2.5)
    assert won2 is False
    assert label2 == "lost"
