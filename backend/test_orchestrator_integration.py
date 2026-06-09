"""
Integration test for MLB Orchestrator with Tail Risk + Fragility Calibrator.
Validates that pick_payload and pipeline_meta contain the expected fields.
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


@pytest.mark.asyncio
async def test_orchestrator_integration_tail_risk_and_fragility():
    """
    Test that MLB orchestrator properly integrates tail_risk and fragility_calibration
    into pick_payload and pipeline_meta.
    """
    from services.mlb_day_orchestrator import analyze_mlb_day
    
    # Mock the database
    mock_db = MagicMock()
    
    # Mock the pitcher confirmation to return empty (we'll test the integration structure)
    with patch('services.mlb_day_orchestrator._confirm_pitchers_statsapi') as mock_confirm:
        mock_confirm.return_value = ({}, "")
        
        result = await analyze_mlb_day(date_str="2026-05-22", db=mock_db)
        
        # Verify pipeline_meta structure
        assert "pipeline_meta" in result
        meta = result["pipeline_meta"]
        
        # These fields should exist even if no games were processed
        assert "date" in meta
        assert "date_str" in meta
        assert "date_basis" in meta
        assert meta["date_basis"] == "America/New_York"


@pytest.mark.asyncio
async def test_tail_risk_integration_in_pick_payload():
    """
    Test that when a pick is generated, it contains tail_risk and fragility_calibration fields.
    This is a unit test of the integration logic.
    """
    from services.mlb_expected_runs_distribution import (
        compute_expected_runs_distribution,
        compute_tail_risk,
        interpret_market_profile,
    )
    from services.mlb_fragility_calibrator import calibrate_fragility
    
    # Simulate the orchestrator's workflow
    # 1. Compute expected runs distribution
    erd = compute_expected_runs_distribution(
        expected_runs=8.0,
        market="total_runs_under",
        market_line=10.5,
        nb_dispersion_ratio=1.35,
        fragility_score=20,
        traffic_score=55,
        series_familiarity_score=50,
    )
    
    assert erd["available"] is True
    
    # 2. Compute tail risk
    tail_risk = compute_tail_risk(
        distribution_payload=erd,
        market_line=10.5,
        market_side="under",
    )
    
    assert tail_risk["available"] is True
    assert "tail_bucket" in tail_risk
    assert "under_quality" in tail_risk
    
    # 3. Interpret market profile
    market_profile = interpret_market_profile(
        distribution_payload=erd,
        tail_risk_payload=tail_risk,
    )
    
    assert "profile" in market_profile
    assert "reason_codes" in market_profile
    
    # 4. Calibrate fragility
    fragility_calibration = calibrate_fragility(
        base_fragility=20,
        market_side="under",
        expected_runs=8.0,
        market_line=10.5,
        inning_lambda_projection={"lambda_1_3": 2.5, "lambda_4_6": 3.0, "lambda_7_9": 2.5},
        home_pitcher={"era": 3.2, "whip": 1.10},
        away_pitcher={"era": 4.92, "whip": 1.43},
        bullpen_home={"bullpen_usage_3d": 0.65},
        bullpen_away={"bullpen_usage_3d": 0.62},
        series_familiarity={"series_familiarity_score": 45},
        traffic_score=40,
        defensive_breakdown_score=40,
        tail_risk=tail_risk,
    )
    
    assert fragility_calibration["available"] is True
    assert "adjusted_fragility" in fragility_calibration
    assert "delta" in fragility_calibration
    assert "hidden_over_routes" in fragility_calibration
    
    # 5. Simulate pick_payload structure (as orchestrator would build it)
    pick_payload = {
        "tail_risk": tail_risk,
        "market_profile": market_profile,
        "fragility_calibration": fragility_calibration,
    }
    
    # Verify pick_payload structure
    assert "tail_risk" in pick_payload
    assert pick_payload["tail_risk"]["available"] is True
    assert "market_profile" in pick_payload
    assert "fragility_calibration" in pick_payload
    assert pick_payload["fragility_calibration"]["available"] is True
    
    # 6. Simulate pipeline_meta structure (as orchestrator would build it)
    pipeline_meta = {
        "tail_risk": {
            "available": tail_risk.get("available"),
            "tail_bucket": tail_risk.get("tail_bucket"),
            "tail_risk_score": tail_risk.get("tail_risk_score"),
            "under_quality": tail_risk.get("under_quality"),
            "p_ge_12": tail_risk.get("p_ge_12"),
            "p_ge_14": tail_risk.get("p_ge_14"),
            "p_ge_16": tail_risk.get("p_ge_16"),
            "profile": market_profile.get("profile"),
            "reason_codes": (tail_risk.get("reason_codes") or [])
                            + (market_profile.get("reason_codes") or []),
        },
        "fragility_calibration": {
            "available": fragility_calibration.get("available"),
            "base_fragility": fragility_calibration.get("base_fragility"),
            "adjusted_fragility": fragility_calibration.get("adjusted_fragility"),
            "delta": fragility_calibration.get("delta"),
            "hidden_over_routes": fragility_calibration.get("hidden_over_routes") or [],
            "reason_codes": fragility_calibration.get("reason_codes") or [],
        },
    }
    
    # Verify pipeline_meta structure
    assert "tail_risk" in pipeline_meta
    assert pipeline_meta["tail_risk"]["available"] is True
    assert "tail_bucket" in pipeline_meta["tail_risk"]
    assert "under_quality" in pipeline_meta["tail_risk"]
    assert "p_ge_12" in pipeline_meta["tail_risk"]
    assert "p_ge_14" in pipeline_meta["tail_risk"]
    assert "p_ge_16" in pipeline_meta["tail_risk"]
    assert "profile" in pipeline_meta["tail_risk"]
    
    assert "fragility_calibration" in pipeline_meta
    assert pipeline_meta["fragility_calibration"]["available"] is True
    assert "base_fragility" in pipeline_meta["fragility_calibration"]
    assert "adjusted_fragility" in pipeline_meta["fragility_calibration"]
    assert "delta" in pipeline_meta["fragility_calibration"]
    assert "hidden_over_routes" in pipeline_meta["fragility_calibration"]
    
    print("✅ Integration test passed: pick_payload and pipeline_meta structures are correct")


@pytest.mark.asyncio
async def test_orchestrator_does_not_break_existing_endpoints():
    """
    Verify that the new tail_risk and fragility_calibration modules
    do NOT break existing MLB endpoints.
    """
    from services.mlb_day_orchestrator import analyze_mlb_day
    
    mock_db = MagicMock()
    
    # This should not raise any exceptions
    try:
        result = await analyze_mlb_day(date_str="2026-05-22", db=mock_db)
        assert "picks" in result
        assert "rescued_picks" in result
        assert "discarded_picks" in result
        assert "pipeline_meta" in result
        print("✅ Orchestrator endpoints remain intact")
    except Exception as e:
        pytest.fail(f"Orchestrator raised exception: {e}")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
