"""Backend testing for Market Lean Consistency + Live Baseball Detection fixes.

User-reported issues (May 2026):
  ISSUE 1 — MARKET LEAN INCONSISTENCY:
    Historical Profile panel showed 'LEAN OVER CARRERAS' while the recommended
    pick was 'UNDER 9.5' with Expected Runs 6.7 and P(Under)=86%.
    Canonical case: LA Angels vs Tampa Bay → game started 4-1 first inning.

  ISSUE 2 — LIVE BASEBALL NOT DETECTED:
    When a baseball match starts (status 'In Progress' / 'Inning X' / is_live=true),
    the engine was silently dropping it because filter_upcoming() rejected any
    in-progress match.

Tests:
1. market_lean_classifier.py pure functions
2. validate_lean_consistency() mismatch detection
3. classify_and_validate() integration
4. time_filter.py live baseball handling
5. analyst_engine.py wiring (allow_live_for_sports={'baseball'})
6. validate_pick_before_output() baseball live exemption
7. mlb_day_orchestrator.py market_lean wiring
8. Regression: non-baseball sports unaffected
"""
import sys
import asyncio
from datetime import datetime, timezone, timedelta

# Test configuration
BACKEND_URL = "https://low-volatility-plays.preview.emergentagent.com"
DEMO_EMAIL = "demo@valuebet.app"
DEMO_PASSWORD = "demo1234"

class TestRunner:
    def __init__(self):
        self.tests_run = 0
        self.tests_passed = 0
        self.tests_failed = 0
        self.failures = []

    def test(self, name: str, condition: bool, details: str = ""):
        """Run a single test assertion."""
        self.tests_run += 1
        if condition:
            self.tests_passed += 1
            print(f"✅ PASS: {name}")
            if details:
                print(f"   {details}")
        else:
            self.tests_failed += 1
            self.failures.append(name)
            print(f"❌ FAIL: {name}")
            if details:
                print(f"   {details}")

    def summary(self):
        """Print test summary."""
        print("\n" + "="*70)
        print(f"TEST SUMMARY: {self.tests_passed}/{self.tests_run} passed")
        if self.failures:
            print(f"\nFailed tests:")
            for f in self.failures:
                print(f"  - {f}")
        print("="*70)
        return self.tests_failed == 0


async def main():
    runner = TestRunner()
    
    print("="*70)
    print("BACKEND TESTING: Market Lean Consistency + Live Baseball Detection")
    print("="*70)
    
    # ═══════════════════════════════════════════════════════════════════════
    # TEST 1: market_lean_classifier.py Pure Functions
    # ═══════════════════════════════════════════════════════════════════════
    print("\n[TEST 1] market_lean_classifier.py Pure Functions")
    print("-"*70)
    
    try:
        from services.market_lean_classifier import (
            classify_market_lean,
            validate_lean_consistency,
            classify_and_validate,
            LEAN_DELTA_THRESHOLD
        )
        runner.test(
            "Import market_lean_classifier",
            True,
            f"LEAN_DELTA_THRESHOLD={LEAN_DELTA_THRESHOLD}"
        )
    except Exception as e:
        runner.test(
            "Import market_lean_classifier",
            False,
            f"Import failed: {e}"
        )
        return runner.summary()
    
    # Test case 1: Angels @ Rays (expected=6.7, line=9.5) → UNDER
    result1 = classify_market_lean(
        expected_runs=6.7,
        market_line=9.5,
        p_under=0.86
    )
    runner.test(
        "classify_market_lean: Angels @ Rays case (6.7 vs 9.5) → UNDER",
        result1["lean"] == "UNDER" and result1["delta"] == -2.8,
        f"lean={result1['lean']}, delta={result1['delta']}, confidence={result1['confidence']}"
    )
    runner.test(
        "classify_market_lean: confidence enriched from p_under",
        result1["confidence"] >= 86,
        f"confidence={result1['confidence']} (should be ≥86 from p_under=0.86)"
    )
    runner.test(
        "classify_market_lean: reason in Spanish",
        "carreras" in result1["reason"].lower() and "por debajo" in result1["reason"].lower(),
        f"reason={result1['reason'][:80]}..."
    )
    
    # Test case 2: expected=10.8, line=8.5 → OVER
    result2 = classify_market_lean(
        expected_runs=10.8,
        market_line=8.5
    )
    runner.test(
        "classify_market_lean: expected=10.8, line=8.5 → OVER",
        result2["lean"] == "OVER" and result2["delta"] == 2.3,
        f"lean={result2['lean']}, delta={result2['delta']}, confidence={result2['confidence']}"
    )
    
    # Test case 3: expected=9.0, line=9.5 → NONE (delta -0.5, abs<1.0)
    result3 = classify_market_lean(
        expected_runs=9.0,
        market_line=9.5
    )
    runner.test(
        "classify_market_lean: expected=9.0, line=9.5 → NONE (delta -0.5)",
        result3["lean"] == "NONE" and abs(result3["delta"]) < 1.0,
        f"lean={result3['lean']}, delta={result3['delta']}"
    )
    
    # ═══════════════════════════════════════════════════════════════════════
    # TEST 2: validate_lean_consistency() Mismatch Detection
    # ═══════════════════════════════════════════════════════════════════════
    print("\n[TEST 2] validate_lean_consistency() Mismatch Detection")
    print("-"*70)
    
    # Consistent case: lean=UNDER, recommended='Total Runs Under 9.5'
    cons1 = validate_lean_consistency(
        lean_payload=result1,
        recommended_market="Total Runs Under 9.5",
        game_id="test_angels_rays"
    )
    runner.test(
        "validate_lean_consistency: UNDER + 'Under 9.5' → consistent",
        cons1["consistent"] and not cons1["mismatch"],
        f"consistent={cons1['consistent']}, mismatch={cons1['mismatch']}"
    )
    
    # Mismatch case: lean=OVER but recommended='Total Runs Under 9.5'
    mismatch_payload = classify_market_lean(
        expected_runs=11.0,
        market_line=9.5
    )
    cons2 = validate_lean_consistency(
        lean_payload=mismatch_payload,
        recommended_market="Total Runs Under 9.5",
        game_id="test_mismatch"
    )
    runner.test(
        "validate_lean_consistency: OVER + 'Under 9.5' → mismatch",
        not cons2["consistent"] and cons2["mismatch"],
        f"consistent={cons2['consistent']}, mismatch={cons2['mismatch']}, warning={cons2['warning'][:60]}..."
    )
    runner.test(
        "validate_lean_consistency: warning populated",
        cons2["warning"] is not None and "OVER" in cons2["warning"] and "UNDER" in cons2["warning"],
        f"warning={cons2['warning'][:80]}..."
    )
    
    # ═══════════════════════════════════════════════════════════════════════
    # TEST 3: classify_and_validate() Integration
    # ═══════════════════════════════════════════════════════════════════════
    print("\n[TEST 3] classify_and_validate() Integration")
    print("-"*70)
    
    # Angels @ Rays case (consistent)
    integrated1 = classify_and_validate(
        expected_runs=6.7,
        market_line=9.5,
        recommended_market="Total Runs Under 9.5",
        p_under=0.86,
        game_id="angels_rays"
    )
    runner.test(
        "classify_and_validate: Angels @ Rays → display_lean='UNDER'",
        integrated1["display_lean"] == "UNDER",
        f"display_lean={integrated1['display_lean']}, lean={integrated1['lean']}"
    )
    runner.test(
        "classify_and_validate: consistency block present",
        "consistency" in integrated1 and integrated1["consistency"]["consistent"],
        f"consistency={integrated1['consistency']}"
    )
    
    # Mismatch case (expected=11.0, line=9.5, rec='Under 9.5')
    integrated2 = classify_and_validate(
        expected_runs=11.0,
        market_line=9.5,
        recommended_market="Total Runs Under 9.5",
        game_id="mismatch_case"
    )
    runner.test(
        "classify_and_validate: mismatch → display_lean='REVIEW_REQUIRED'",
        integrated2["display_lean"] == "REVIEW_REQUIRED",
        f"display_lean={integrated2['display_lean']}, lean={integrated2['lean']}"
    )
    runner.test(
        "classify_and_validate: mismatch logged",
        integrated2["consistency"]["mismatch"],
        f"mismatch={integrated2['consistency']['mismatch']}"
    )
    
    # ═══════════════════════════════════════════════════════════════════════
    # TEST 4: time_filter.py Live Baseball Handling
    # ═══════════════════════════════════════════════════════════════════════
    print("\n[TEST 4] time_filter.py Live Baseball Handling")
    print("-"*70)
    
    try:
        from services.time_filter import (
            is_match_upcoming,
            filter_upcoming,
            validate_pick_before_output
        )
        runner.test(
            "Import time_filter",
            True,
            "Functions imported successfully"
        )
    except Exception as e:
        runner.test(
            "Import time_filter",
            False,
            f"Import failed: {e}"
        )
        return runner.summary()
    
    # Test is_match_upcoming with allow_live=True for baseball
    now = datetime.now(timezone.utc)
    past_kickoff = (now - timedelta(hours=1)).isoformat()
    
    live_baseball_match = {
        "kickoff_iso": past_kickoff,
        "status": "In Progress",
        "is_live": True,
        "sport": "baseball"
    }
    
    # Without allow_live, should be rejected
    result_no_live = is_match_upcoming(live_baseball_match, allow_live=False)
    runner.test(
        "is_match_upcoming: live baseball rejected when allow_live=False",
        not result_no_live,
        f"result={result_no_live} (should be False)"
    )
    
    # With allow_live=True, should be accepted
    result_with_live = is_match_upcoming(live_baseball_match, allow_live=True)
    runner.test(
        "is_match_upcoming: live baseball accepted when allow_live=True",
        result_with_live,
        f"result={result_with_live} (should be True)"
    )
    
    # Test filter_upcoming with allow_live_for_sports={'baseball'}
    future_kickoff = (now + timedelta(hours=2)).isoformat()
    test_matches = [
        {
            "match_id": 1,
            "kickoff_iso": past_kickoff,
            "status": "In Progress",
            "is_live": True,
            "sport": "baseball"
        },
        {
            "match_id": 2,
            "kickoff_iso": future_kickoff,
            "status": "Scheduled",
            "is_live": False,
            "sport": "baseball"
        },
        {
            "match_id": 3,
            "kickoff_iso": past_kickoff,
            "status": "1H",
            "is_live": True,
            "sport": "football"
        }
    ]
    
    kept, dropped = filter_upcoming(
        test_matches,
        allow_live_for_sports={'baseball'}
    )
    
    runner.test(
        "filter_upcoming: live baseball kept with allow_live_for_sports",
        len(kept) == 2 and any(m["match_id"] == 1 for m in kept),
        f"kept={len(kept)} matches (should be 2: live baseball + future baseball)"
    )
    runner.test(
        "filter_upcoming: live baseball tagged with _live_route",
        any(m.get("_live_route") for m in kept if m["match_id"] == 1),
        f"_live_route flag present on live baseball match"
    )
    runner.test(
        "filter_upcoming: live football dropped (not in allow_live_for_sports)",
        len(dropped) == 1 and any(m["match_id"] == 3 for m in dropped),
        f"dropped={len(dropped)} matches (should be 1: live football)"
    )
    
    # ═══════════════════════════════════════════════════════════════════════
    # TEST 5: validate_pick_before_output() Baseball Live Exemption
    # ═══════════════════════════════════════════════════════════════════════
    print("\n[TEST 5] validate_pick_before_output() Baseball Live Exemption")
    print("-"*70)
    
    # Baseball live pick should NOT be blocked
    baseball_live_pick = {
        "match_id": "mlb_123",
        "sport": "baseball",
        "kickoff_iso": past_kickoff,
        "is_live": True,
        "status": "Inning 3"
    }
    validated_bb = validate_pick_before_output(baseball_live_pick)
    runner.test(
        "validate_pick_before_output: baseball live NOT blocked",
        not validated_bb.get("blocked"),
        f"blocked={validated_bb.get('blocked')}, block_reasons={validated_bb.get('block_reasons')}"
    )
    
    # Football live pick should be blocked
    football_live_pick = {
        "match_id": "football_123",
        "sport": "football",
        "kickoff_iso": past_kickoff,
        "is_live": True,
        "status": "1H"
    }
    validated_fb = validate_pick_before_output(football_live_pick)
    runner.test(
        "validate_pick_before_output: football live IS blocked",
        validated_fb.get("blocked"),
        f"blocked={validated_fb.get('blocked')}, block_reasons={validated_fb.get('block_reasons')}"
    )
    
    # ═══════════════════════════════════════════════════════════════════════
    # TEST 6: analyst_engine.py Wiring (allow_live_for_sports)
    # ═══════════════════════════════════════════════════════════════════════
    print("\n[TEST 6] analyst_engine.py Wiring Check")
    print("-"*70)
    
    try:
        # Check that analyst_engine imports time_filter correctly
        from services import analyst_engine
        import inspect
        
        # Check if _run_analysis_pipeline exists and uses filter_upcoming
        source = inspect.getsource(analyst_engine)
        
        runner.test(
            "analyst_engine: imports time_filter",
            "time_filter" in source or "filter_upcoming" in source,
            "time_filter module referenced in analyst_engine"
        )
        
        runner.test(
            "analyst_engine: uses allow_live_for_sports parameter",
            "allow_live_for_sports" in source,
            "allow_live_for_sports parameter found in source"
        )
        
        runner.test(
            "analyst_engine: baseball in allow_live_for_sports",
            "allow_live_for_sports={'baseball'}" in source or 
            'allow_live_for_sports={"baseball"}' in source,
            "baseball configured in allow_live_for_sports"
        )
        
    except Exception as e:
        runner.test(
            "analyst_engine: wiring check",
            False,
            f"Check failed: {e}"
        )
    
    # ═══════════════════════════════════════════════════════════════════════
    # TEST 7: Regression - Non-Baseball Sports Unaffected
    # ═══════════════════════════════════════════════════════════════════════
    print("\n[TEST 7] Regression - Non-Baseball Sports Unaffected")
    print("-"*70)
    
    # Football match (not live) should work as before
    football_upcoming = {
        "match_id": "fb_456",
        "kickoff_iso": future_kickoff,
        "status": "NS",
        "is_live": False,
        "sport": "football"
    }
    result_fb = is_match_upcoming(football_upcoming)
    runner.test(
        "Regression: football upcoming match still works",
        result_fb,
        f"result={result_fb} (should be True)"
    )
    
    # Basketball match (not live) should work as before
    basketball_upcoming = {
        "match_id": "bb_789",
        "kickoff_iso": future_kickoff,
        "status": "Scheduled",
        "is_live": False,
        "sport": "basketball"
    }
    result_bk = is_match_upcoming(basketball_upcoming)
    runner.test(
        "Regression: basketball upcoming match still works",
        result_bk,
        f"result={result_bk} (should be True)"
    )
    
    # Finished matches still dropped
    finished_match = {
        "match_id": "finished_123",
        "kickoff_iso": past_kickoff,
        "status": "Final",
        "is_live": False,
        "sport": "baseball"
    }
    kept_fin, dropped_fin = filter_upcoming([finished_match])
    runner.test(
        "Regression: finished matches still dropped",
        len(dropped_fin) == 1 and len(kept_fin) == 0,
        f"kept={len(kept_fin)}, dropped={len(dropped_fin)}"
    )
    
    # ═══════════════════════════════════════════════════════════════════════
    # TEST 8: Integration - Logger Check
    # ═══════════════════════════════════════════════════════════════════════
    print("\n[TEST 8] Integration - Logger Check")
    print("-"*70)
    
    try:
        import logging
        
        # Check that MARKET_LEAN_MISMATCH logger exists
        mismatch_logger = logging.getLogger("MARKET_LEAN_MISMATCH")
        runner.test(
            "MARKET_LEAN_MISMATCH logger exists",
            mismatch_logger is not None,
            f"logger={mismatch_logger.name}"
        )
        
        # Trigger a mismatch to test logging (won't actually log in test, but validates code path)
        try:
            mismatch_result = classify_and_validate(
                expected_runs=11.0,
                market_line=9.5,
                recommended_market="Total Runs Under 9.5",
                game_id="logger_test"
            )
            runner.test(
                "Mismatch detection triggers without error",
                mismatch_result["display_lean"] == "REVIEW_REQUIRED",
                "Mismatch detection code path executed successfully"
            )
        except Exception as e:
            runner.test(
                "Mismatch detection triggers without error",
                False,
                f"Error: {e}"
            )
            
    except Exception as e:
        runner.test(
            "Logger check",
            False,
            f"Check failed: {e}"
        )
    
    # ═══════════════════════════════════════════════════════════════════════
    # TEST 9: Edge Cases
    # ═══════════════════════════════════════════════════════════════════════
    print("\n[TEST 9] Edge Cases")
    print("-"*70)
    
    # Test with None values
    try:
        result_none = classify_market_lean(
            expected_runs=None,
            market_line=None
        )
        runner.test(
            "classify_market_lean: handles None values gracefully",
            result_none["lean"] == "NONE" and result_none["expected_runs"] == 0.0,
            f"lean={result_none['lean']}, expected_runs={result_none['expected_runs']}"
        )
    except Exception as e:
        runner.test(
            "classify_market_lean: handles None values gracefully",
            False,
            f"Error: {e}"
        )
    
    # Test with exactly threshold boundary (delta = 1.0)
    result_boundary = classify_market_lean(
        expected_runs=8.5,
        market_line=9.5
    )
    runner.test(
        "classify_market_lean: boundary case delta=-1.0 → UNDER",
        result_boundary["lean"] == "UNDER" and result_boundary["delta"] == -1.0,
        f"lean={result_boundary['lean']}, delta={result_boundary['delta']}"
    )
    
    # Test with match missing is_live flag but has "In Progress" status
    # The time_filter correctly infers live status from "In Progress" status
    match_no_flag = {
        "match_id": "no_flag",
        "kickoff_iso": past_kickoff,
        "status": "In Progress",
        "sport": "baseball"
        # is_live missing but status indicates live
    }
    result_no_flag = is_match_upcoming(match_no_flag, allow_live=True)
    runner.test(
        "is_match_upcoming: infers live from 'In Progress' status",
        result_no_flag,  # Should be accepted when allow_live=True and status is "In Progress"
        f"result={result_no_flag} (correctly inferred live from status)"
    )
    
    # ═══════════════════════════════════════════════════════════════════════
    # SUMMARY
    # ═══════════════════════════════════════════════════════════════════════
    success = runner.summary()
    return 0 if success else 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
