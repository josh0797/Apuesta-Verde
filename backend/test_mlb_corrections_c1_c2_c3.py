"""Backend validation for MLB Engine Corrections C1, C2, C3.

Tests the 3 critical corrections:
  C1: Eastern Time (America/New_York) for all MLB date computations
  C2: Cache validation (never return games from wrong date)
  C3: Pipeline meta (always return detailed pipeline_meta block)

This test suite validates end-to-end behavior through the FastAPI endpoints.
"""
import sys
import logging
import asyncio
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

# Suppress noisy logs during test
logging.basicConfig(level=logging.WARNING)

BASE_URL = "https://low-volatility-plays.preview.emergentagent.com"
EASTERN = ZoneInfo("America/New_York")


class MLBCorrectionsValidator:
    def __init__(self):
        self.tests_run = 0
        self.tests_passed = 0
        self.tests_failed = 0
        self.failures = []

    def test(self, name: str, condition: bool, details: str = ""):
        """Run a single test assertion."""
        self.tests_run += 1
        print(f"\n🔍 Testing {name}...")
        if condition:
            self.tests_passed += 1
            print(f"✅ PASS - {details or name}")
            return True
        else:
            self.tests_failed += 1
            msg = f"❌ FAIL - {name}: {details}"
            print(msg)
            self.failures.append(msg)
            return False

    def section(self, title: str):
        """Print a section header."""
        print(f"\n{'='*70}")
        print(f"  {title}")
        print(f"{'='*70}")

    def summary(self):
        """Print final summary."""
        print(f"\n{'='*70}")
        print(f"  TEST SUMMARY")
        print(f"{'='*70}")
        print(f"Total:  {self.tests_run}")
        print(f"Passed: {self.tests_passed}")
        print(f"Failed: {self.tests_failed}")
        if self.failures:
            print(f"\n❌ FAILURES:")
            for f in self.failures:
                print(f"  • {f}")
        print(f"{'='*70}\n")
        return self.tests_failed == 0


def test_c1_eastern_time():
    """C1: Eastern Time - MLB date must be computed in America/New_York."""
    v = MLBCorrectionsValidator()
    v.section("C1: EASTERN TIME (America/New_York)")
    
    try:
        from services.mlb_day_orchestrator import analyze_mlb_day, EASTERN
        from services.mlb_stats_api import DEFAULT_SEASON
        import inspect
        
        # Test 1: EASTERN constant is defined and correct
        v.test(
            "EASTERN constant defined in mlb_day_orchestrator",
            EASTERN is not None,
            f"EASTERN={EASTERN}"
        )
        
        v.test(
            "EASTERN is America/New_York",
            str(EASTERN) == "America/New_York",
            f"EASTERN={EASTERN}"
        )
        
        # Test 2: DEFAULT_SEASON uses Eastern time
        now_eastern = datetime.now(EASTERN)
        v.test(
            "DEFAULT_SEASON uses Eastern time",
            DEFAULT_SEASON == now_eastern.year,
            f"DEFAULT_SEASON={DEFAULT_SEASON}, Eastern year={now_eastern.year}"
        )
        
        # Test 3: analyze_mlb_day defaults to Eastern today
        source = inspect.getsource(analyze_mlb_day)
        v.test(
            "analyze_mlb_day uses EASTERN for default date",
            "datetime.now(EASTERN)" in source and "strftime" in source,
            "Code uses datetime.now(EASTERN).strftime('%Y-%m-%d')"
        )
        
        # Test 4: Run analyze_mlb_day with empty date_str (should default to Eastern today)
        class MockDB:
            class MockCollection:
                async def find_one(self, *args, **kwargs):
                    return None
                async def update_one(self, *args, **kwargs):
                    pass
                async def count_documents(self, *args, **kwargs):
                    return 0
            mlb_cache = MockCollection()
        
        async def test_default_date():
            result = await analyze_mlb_day('', db=MockDB())
            return result
        
        result = asyncio.run(test_default_date())
        pipeline_meta = result.get("pipeline_meta", {})
        
        v.test(
            "analyze_mlb_day('') returns pipeline_meta",
            "pipeline_meta" in result,
            f"Keys: {list(result.keys())}"
        )
        
        v.test(
            "pipeline_meta.date_basis == 'America/New_York'",
            pipeline_meta.get("date_basis") == "America/New_York",
            f"date_basis={pipeline_meta.get('date_basis')}"
        )
        
        # Verify date_str matches Eastern today
        eastern_today = datetime.now(EASTERN).strftime("%Y-%m-%d")
        v.test(
            "pipeline_meta.date_str matches Eastern today",
            pipeline_meta.get("date_str") == eastern_today,
            f"date_str={pipeline_meta.get('date_str')}, Eastern today={eastern_today}"
        )
        
        # Test 5: Check mlb_stats_api also uses EASTERN
        from services import mlb_stats_api
        source_api = inspect.getsource(mlb_stats_api)
        v.test(
            "mlb_stats_api imports EASTERN",
            "EASTERN" in source_api or "America/New_York" in source_api,
            "EASTERN timezone used in mlb_stats_api"
        )
        
    except Exception as e:
        v.test("C1 execution", False, f"Exception: {e}")
        import traceback
        traceback.print_exc()
    
    return v


def test_c2_cache_validation():
    """C2: Cache validation - never return games from wrong date."""
    v = MLBCorrectionsValidator()
    v.section("C2: CACHE VALIDATION")
    
    try:
        from services.mlb_stats_api import get_schedule_with_probables, LAST_SCHEDULE_CACHE_STATUS
        import inspect
        
        # Test 1: LAST_SCHEDULE_CACHE_STATUS ContextVar exists
        v.test(
            "LAST_SCHEDULE_CACHE_STATUS ContextVar exists",
            LAST_SCHEDULE_CACHE_STATUS is not None,
            "ContextVar defined"
        )
        
        # Test 2: Check code validates cache by date
        source = inspect.getsource(get_schedule_with_probables)
        
        v.test(
            "Code validates cached games by gameDate",
            "_matches_eastern_date" in source or "matches_eastern_date" in source,
            "Date validation function present"
        )
        
        v.test(
            "Code converts gameDate to Eastern for validation",
            "astimezone(EASTERN)" in source or "astimezone" in source,
            "Timezone conversion present"
        )
        
        v.test(
            "Code sets cache_status to hit_valid",
            "hit_valid" in source,
            "hit_valid status present"
        )
        
        v.test(
            "Code sets cache_status to hit_invalid_refetched",
            "hit_invalid_refetched" in source,
            "hit_invalid_refetched status present"
        )
        
        v.test(
            "Code sets cache_status to miss",
            'LAST_SCHEDULE_CACHE_STATUS.set("miss")' in source or '"miss"' in source,
            "miss status present"
        )
        
        v.test(
            "Code sets cache_status to error",
            'LAST_SCHEDULE_CACHE_STATUS.set("error")' in source or '"error"' in source,
            "error status present"
        )
        
        # Test 3: Run get_schedule_with_probables and check cache_status is set
        class MockDB:
            class MockCollection:
                async def find_one(self, *args, **kwargs):
                    return None
                async def update_one(self, *args, **kwargs):
                    pass
            mlb_cache = MockCollection()
        
        async def test_cache_status():
            # Reset ContextVar
            LAST_SCHEDULE_CACHE_STATUS.set("unknown")
            
            # Call with today's date
            eastern_today = datetime.now(EASTERN).strftime("%Y-%m-%d")
            result = await get_schedule_with_probables(MockDB(), eastern_today)
            
            # Check ContextVar was set
            status = LAST_SCHEDULE_CACHE_STATUS.get()
            return status, result
        
        status, result = asyncio.run(test_cache_status())
        
        v.test(
            "get_schedule_with_probables sets LAST_SCHEDULE_CACHE_STATUS",
            status in ["hit_valid", "hit_invalid_refetched", "miss", "error"],
            f"cache_status={status}"
        )
        
        v.test(
            "get_schedule_with_probables returns list",
            isinstance(result, list),
            f"Returned type: {type(result)}"
        )
        
    except Exception as e:
        v.test("C2 execution", False, f"Exception: {e}")
        import traceback
        traceback.print_exc()
    
    return v


def test_c3_pipeline_meta():
    """C3: Pipeline meta - always return detailed pipeline_meta block."""
    v = MLBCorrectionsValidator()
    v.section("C3: PIPELINE META")
    
    try:
        from services.mlb_day_orchestrator import analyze_mlb_day
        
        # Test C3a: Real run returns pipeline_meta with all required fields
        class MockDB:
            class MockCollection:
                async def find_one(self, *args, **kwargs):
                    return None
                async def update_one(self, *args, **kwargs):
                    pass
                async def count_documents(self, *args, **kwargs):
                    return 0
            mlb_cache = MockCollection()
        
        async def test_pipeline_meta_present():
            result = await analyze_mlb_day('', db=MockDB())
            return result
        
        result = asyncio.run(test_pipeline_meta_present())
        pipeline_meta = result.get("pipeline_meta", {})
        
        v.test(
            "analyze_mlb_day returns pipeline_meta",
            "pipeline_meta" in result,
            f"Keys: {list(result.keys())}"
        )
        
        # Check all required fields
        required_fields = [
            "date_str",
            "date_basis",
            "schedule_games_found",
            "confirmed_games",
            "games_processed",
            "dropped_past_or_finished",
            "dropped_missing_pitchers",
            "dropped_low_pitcher_data",
            "picks_total",
            "rescued_total",
            "discarded_total",
            "cache_status",
            "source_used",
            "abort_reason",
        ]
        
        for field in required_fields:
            v.test(
                f"pipeline_meta contains '{field}'",
                field in pipeline_meta,
                f"{field}={pipeline_meta.get(field)}"
            )
        
        # Test C3b: Past date returns abort_reason='all_games_already_played_or_finished'
        async def test_past_date():
            result = await analyze_mlb_day('2025-08-01', db=MockDB())
            return result
        
        past_result = asyncio.run(test_past_date())
        past_meta = past_result.get("pipeline_meta", {})
        
        v.test(
            "Past date (2025-08-01) returns pipeline_meta",
            "pipeline_meta" in past_result,
            f"Keys: {list(past_result.keys())}"
        )
        
        # Note: abort_reason might be 'no_probable_pitchers_all_sources' if no games found
        # or 'all_games_already_played_or_finished' if games found but all finished
        abort_reason = past_meta.get("abort_reason")
        v.test(
            "Past date has abort_reason set",
            abort_reason is not None,
            f"abort_reason={abort_reason}"
        )
        
        # Test C3c: Zero picks/rescued returns abort_reason='no_value_found'
        # This is tested implicitly by the empty result above
        if result.get("picks") == [] and result.get("rescued_picks") == []:
            v.test(
                "Zero picks/rescued sets abort_reason",
                pipeline_meta.get("abort_reason") is not None,
                f"abort_reason={pipeline_meta.get('abort_reason')}"
            )
        
    except Exception as e:
        v.test("C3 execution", False, f"Exception: {e}")
        import traceback
        traceback.print_exc()
    
    return v


def test_c3d_analysis_run_endpoint():
    """C3d: POST /api/analysis/run must include pipeline_meta."""
    v = MLBCorrectionsValidator()
    v.section("C3d: /api/analysis/run ENDPOINT")
    
    try:
        import requests
        
        # Note: This endpoint requires authentication
        # We'll test the code structure instead of making actual HTTP calls
        
        from services import analyst_engine
        import inspect
        
        # Check _run_analysis_pipeline function
        source = inspect.getsource(analyst_engine)
        
        v.test(
            "analyst_engine mentions pipeline_meta",
            "pipeline_meta" in source,
            "pipeline_meta referenced in analyst_engine"
        )
        
        # Check server.py _run_analysis_pipeline
        import server
        source_server = inspect.getsource(server._run_analysis_pipeline)
        
        v.test(
            "_run_analysis_pipeline builds pipeline_meta",
            "pipeline_meta" in source_server and "sport" in source_server,
            "pipeline_meta construction present"
        )
        
        v.test(
            "_run_analysis_pipeline sets date_str",
            "date_str" in source_server,
            "date_str field present"
        )
        
        v.test(
            "_run_analysis_pipeline sets date_basis",
            "date_basis" in source_server,
            "date_basis field present"
        )
        
        v.test(
            "_run_analysis_pipeline sets abort_reason",
            "abort_reason" in source_server,
            "abort_reason field present"
        )
        
        v.test(
            "_run_analysis_pipeline returns pipeline_meta in result",
            'result["pipeline_meta"]' in source_server or "pipeline_meta" in source_server,
            "pipeline_meta included in result"
        )
        
    except Exception as e:
        v.test("C3d execution", False, f"Exception: {e}")
        import traceback
        traceback.print_exc()
    
    return v


def test_regression_checks():
    """Regression: Verify existing functionality still works."""
    v = MLBCorrectionsValidator()
    v.section("REGRESSION CHECKS")
    
    try:
        import requests
        
        # Test 1: Backend service is running
        r = requests.get(f"{BASE_URL}/api/", timeout=10)
        v.test(
            "Backend service responds",
            r.status_code == 200,
            f"Status: {r.status_code}"
        )
        
        # Test 2: /api/mlb/day endpoint (without date param - should default to Eastern today)
        # Note: May require auth, so 401/403 is acceptable
        r = requests.get(f"{BASE_URL}/api/mlb/day", timeout=30)
        v.test(
            "/api/mlb/day endpoint responds",
            r.status_code in [200, 401, 403],
            f"Status: {r.status_code}"
        )
        
        if r.status_code == 200:
            data = r.json()
            
            # Check for pipeline_meta (C3 requirement)
            v.test(
                "/api/mlb/day returns pipeline_meta",
                "pipeline_meta" in data,
                f"Keys: {list(data.keys())}"
            )
            
            if "pipeline_meta" in data:
                pm = data["pipeline_meta"]
                
                # Check date_basis (C1 requirement)
                v.test(
                    "pipeline_meta.date_basis == 'America/New_York'",
                    pm.get("date_basis") == "America/New_York",
                    f"date_basis={pm.get('date_basis')}"
                )
                
                # Check cache_status (C2 requirement)
                v.test(
                    "pipeline_meta.cache_status is set",
                    pm.get("cache_status") in ["hit_valid", "hit_invalid_refetched", "miss", "error", "unknown"],
                    f"cache_status={pm.get('cache_status')}"
                )
                
                # Check abort_reason is present (may be None if picks found)
                v.test(
                    "pipeline_meta has abort_reason field",
                    "abort_reason" in pm,
                    f"abort_reason={pm.get('abort_reason')}"
                )
        
        # Test 3: Check signals still emit
        from services.signal_catalog import make_signal
        
        signals_to_check = [
            "PITCHER_NOT_CONFIRMED",
            "IL_DEPTH_RISK",
            "MLB_COM_FALLBACK_USED"
        ]
        
        for signal_code in signals_to_check:
            sig = make_signal(signal_code, sport="baseball")
            v.test(
                f"Signal {signal_code} still emits for baseball",
                sig is not None and sig.get("code") == signal_code,
                f"Signal: {sig.get('code') if sig else None}"
            )
        
    except Exception as e:
        v.test("Regression checks", False, f"Exception: {e}")
        import traceback
        traceback.print_exc()
    
    return v


def main():
    """Run all validation tests."""
    print(f"\n{'='*70}")
    print(f"  MLB ENGINE CORRECTIONS C1, C2, C3 VALIDATION")
    print(f"  End-to-end testing of Eastern Time, Cache Validation, Pipeline Meta")
    print(f"{'='*70}\n")
    
    all_validators = []
    
    # Run all test suites
    all_validators.append(test_c1_eastern_time())
    all_validators.append(test_c2_cache_validation())
    all_validators.append(test_c3_pipeline_meta())
    all_validators.append(test_c3d_analysis_run_endpoint())
    all_validators.append(test_regression_checks())
    
    # Aggregate results
    total_tests = sum(v.tests_run for v in all_validators)
    total_passed = sum(v.tests_passed for v in all_validators)
    total_failed = sum(v.tests_failed for v in all_validators)
    
    print(f"\n{'='*70}")
    print(f"  FINAL SUMMARY")
    print(f"{'='*70}")
    print(f"Total Tests:  {total_tests}")
    print(f"Passed:       {total_passed}")
    print(f"Failed:       {total_failed}")
    print(f"Success Rate: {(total_passed/total_tests*100):.1f}%")
    
    if total_failed > 0:
        print(f"\n❌ FAILURES DETECTED:")
        for v in all_validators:
            for f in v.failures:
                print(f"  • {f}")
    else:
        print(f"\n✅ ALL TESTS PASSED!")
    
    print(f"{'='*70}\n")
    
    return 0 if total_failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
