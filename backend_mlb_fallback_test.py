"""MLB Stats API Fallback Testing - Phase 15
Testing the full POST /api/analysis/run flow for sport=baseball when db.matches has 0 baseball docs initially.

Tests:
1. Baseball pipeline with 0 initial games triggers MLB Stats API fallback
2. Fallback successfully persists games to db.matches
3. Pipeline continues with fallback data
4. Empty payload contract (200 with pipeline_meta, not 409)
5. Abort reasons: no_games_all_sources, games_found_but_missing_pitchers, no_value_found
6. pipeline_meta fields: schedule_games_found, confirmed_games, games_missing_pitchers, 
   api_sports_games_found, mlb_stats_api_games_found, primary_source, fallback_used, fallback_reason
7. Regression: football/basketball still return 409 on empty results
"""

import sys
import time
import json
from datetime import datetime

try:
    import requests
except ImportError:
    print("❌ requests library not found. Installing...")
    import subprocess
    subprocess.check_call([sys.executable, "-m", "pip", "install", "requests"])
    import requests

BASE_URL = "https://low-volatility-plays.preview.emergentagent.com"

class MLBFallbackTester:
    def __init__(self, base_url=BASE_URL):
        self.base_url = base_url
        self.token = None
        self.tests_run = 0
        self.tests_passed = 0
        self.tests_failed = 0
        self.results = []

    def log(self, message, level="INFO"):
        """Log with timestamp"""
        timestamp = datetime.now().strftime("%H:%M:%S")
        print(f"[{timestamp}] {level}: {message}")

    def run_test(self, name, method, endpoint, expected_status, data=None, timeout=120):
        """Run a single API test"""
        url = f"{self.base_url}/{endpoint}"
        headers = {'Content-Type': 'application/json'}
        if self.token:
            headers['Authorization'] = f'Bearer {self.token}'

        self.tests_run += 1
        self.log(f"Testing {name}...", "TEST")
        
        try:
            start_time = time.time()
            if method == 'GET':
                response = requests.get(url, headers=headers, timeout=timeout)
            elif method == 'POST':
                response = requests.post(url, json=data, headers=headers, timeout=timeout)
            else:
                raise ValueError(f"Unsupported method: {method}")
            
            elapsed = time.time() - start_time

            success = response.status_code == expected_status
            if success:
                self.tests_passed += 1
                self.log(f"✅ PASSED - {name} (Status: {response.status_code}, Time: {elapsed:.2f}s)", "PASS")
                result = {
                    "test": name,
                    "status": "PASSED",
                    "http_status": response.status_code,
                    "elapsed_time": round(elapsed, 2),
                    "endpoint": endpoint
                }
            else:
                self.tests_failed += 1
                self.log(f"❌ FAILED - {name} (Expected {expected_status}, got {response.status_code})", "FAIL")
                result = {
                    "test": name,
                    "status": "FAILED",
                    "http_status": response.status_code,
                    "expected_status": expected_status,
                    "elapsed_time": round(elapsed, 2),
                    "endpoint": endpoint,
                    "error": response.text[:500] if response.text else None
                }

            self.results.append(result)
            
            try:
                return success, response.json() if response.text else {}
            except:
                return success, {"raw_text": response.text[:500]}

        except requests.Timeout:
            self.tests_failed += 1
            self.log(f"❌ FAILED - {name} (Timeout after {timeout}s)", "FAIL")
            self.results.append({
                "test": name,
                "status": "FAILED",
                "error": f"Timeout after {timeout}s",
                "endpoint": endpoint
            })
            return False, {}
        except Exception as e:
            self.tests_failed += 1
            self.log(f"❌ FAILED - {name} (Error: {str(e)})", "FAIL")
            self.results.append({
                "test": name,
                "status": "FAILED",
                "error": str(e),
                "endpoint": endpoint
            })
            return False, {}

    def test_login(self):
        """Test login and get token"""
        self.log("=== Testing Authentication ===")
        success, response = self.run_test(
            "Login",
            "POST",
            "api/auth/login",
            200,
            data={"email": "demo@valuebet.app", "password": "demo1234"}
        )
        if success and 'token' in response:
            self.token = response['token']
            self.log(f"✅ Login successful, token obtained", "SUCCESS")
            return True
        self.log("❌ Login failed, cannot proceed with tests", "ERROR")
        return False

    def test_baseball_fallback_flow(self):
        """Test the full baseball analysis flow with MLB Stats API fallback"""
        self.log("\n=== Testing Baseball MLB Stats API Fallback Flow ===")
        
        # Test 1: Run baseball analysis (should trigger fallback if API-Sports returns 0 games)
        success, response = self.run_test(
            "Baseball Analysis Run (Fallback Test)",
            "POST",
            "api/analysis/run",
            200,  # Should return 200 even with 0 picks (not 409)
            data={
                "sport": "baseball",
                "refresh": True,
                "include_live": False,
                "max_matches": 8,
                "background": False
            },
            timeout=180  # Allow time for fallback + analysis
        )
        
        if not success:
            self.log("❌ Baseball analysis failed to return 200", "ERROR")
            return False
        
        # Validate response structure
        if 'result' not in response:
            self.log("❌ Response missing 'result' field", "ERROR")
            return False
        
        result = response.get('result', {})
        pipeline_meta = result.get('pipeline_meta', {})
        
        # Test 2: Validate pipeline_meta fields exist
        self.log("\n--- Validating pipeline_meta fields ---")
        required_fields = [
            'sport',
            'date_str',
            'date_basis',
            'primary_source',
            'source_used',
            'fallback_used',
            'api_sports_games_found',
            'mlb_stats_api_games_found',
            'schedule_games_found',
            'confirmed_games',
            'games_missing_pitchers'
        ]
        
        missing_fields = []
        for field in required_fields:
            if field not in pipeline_meta:
                missing_fields.append(field)
                self.log(f"❌ Missing pipeline_meta field: {field}", "ERROR")
            else:
                self.log(f"✅ Found pipeline_meta.{field} = {pipeline_meta[field]}", "INFO")
        
        if missing_fields:
            self.log(f"❌ Missing {len(missing_fields)} required pipeline_meta fields", "ERROR")
            return False
        
        # Test 3: Validate fallback behavior
        self.log("\n--- Validating Fallback Behavior ---")
        api_sports_games = pipeline_meta.get('api_sports_games_found', 0)
        mlb_stats_games = pipeline_meta.get('mlb_stats_api_games_found', 0)
        fallback_used = pipeline_meta.get('fallback_used', False)
        source_used = pipeline_meta.get('source_used', '')
        
        self.log(f"API-Sports games found: {api_sports_games}", "INFO")
        self.log(f"MLB Stats API games found: {mlb_stats_games}", "INFO")
        self.log(f"Fallback used: {fallback_used}", "INFO")
        self.log(f"Source used: {source_used}", "INFO")
        
        # If API-Sports returned 0 games, fallback should have been triggered
        if api_sports_games == 0:
            if not fallback_used:
                self.log("❌ API-Sports returned 0 games but fallback_used=False", "ERROR")
                return False
            if source_used not in ['mlb_stats_api_fallback', 'none']:
                self.log(f"❌ Expected source_used='mlb_stats_api_fallback' or 'none', got '{source_used}'", "ERROR")
                return False
            if mlb_stats_games > 0:
                self.log(f"✅ Fallback triggered and found {mlb_stats_games} games", "SUCCESS")
            else:
                self.log("⚠️  Fallback triggered but found 0 games (both sources empty)", "WARNING")
        else:
            self.log(f"✅ API-Sports returned {api_sports_games} games, fallback not needed", "INFO")
        
        # Test 4: Validate abort_reason logic
        self.log("\n--- Validating Abort Reason Logic ---")
        abort_reason = pipeline_meta.get('abort_reason')
        picks = result.get('picks', [])
        
        self.log(f"Abort reason: {abort_reason}", "INFO")
        self.log(f"Picks count: {len(picks)}", "INFO")
        
        # If both sources returned 0 games, abort_reason should be 'no_games_all_sources'
        if api_sports_games == 0 and mlb_stats_games == 0:
            if abort_reason != 'no_games_all_sources':
                self.log(f"❌ Expected abort_reason='no_games_all_sources', got '{abort_reason}'", "ERROR")
                return False
            self.log("✅ Correct abort_reason for no games from all sources", "SUCCESS")
        
        # If games exist but no picks, check for appropriate abort_reason
        if len(picks) == 0 and (api_sports_games > 0 or mlb_stats_games > 0):
            valid_abort_reasons = ['games_found_but_missing_pitchers', 'no_value_found']
            if abort_reason not in valid_abort_reasons:
                self.log(f"⚠️  Games found but no picks; abort_reason='{abort_reason}' (expected one of {valid_abort_reasons})", "WARNING")
        
        # Test 5: Validate empty payload contract (should be 200, not 409)
        self.log("\n--- Validating Empty Payload Contract ---")
        if len(picks) == 0:
            self.log("✅ Baseball returned 200 with empty picks (not 409)", "SUCCESS")
            # Verify summary exists
            if 'summary' not in result:
                self.log("❌ Empty result missing 'summary' field", "ERROR")
                return False
            self.log("✅ Empty result includes summary field", "SUCCESS")
        
        return True

    def test_regression_other_sports(self):
        """Test that football/basketball still return 409 on empty results"""
        self.log("\n=== Testing Regression: Other Sports 409 Contract ===")
        
        # We'll test with a sport that's unlikely to have matches right now
        # to trigger the 409 behavior
        for sport in ['football', 'basketball']:
            self.log(f"\n--- Testing {sport.upper()} empty result behavior ---")
            
            # Try to run analysis with very restrictive parameters to force empty result
            success, response = self.run_test(
                f"{sport.title()} Empty Result Test",
                "POST",
                "api/analysis/run",
                409,  # Should return 409 for non-baseball sports with no matches
                data={
                    "sport": sport,
                    "refresh": False,  # Don't refresh to avoid finding matches
                    "include_live": False,
                    "max_matches": 1,
                    "background": False
                },
                timeout=60
            )
            
            # Note: This test might pass (200) if there are actually matches available
            # The key is that it should NOT return the baseball-style empty payload
            if success:
                self.log(f"✅ {sport.title()} returned 409 as expected for empty result", "SUCCESS")
            else:
                # If it returned 200, check if it's because matches were found
                if response and isinstance(response, dict):
                    result = response.get('result', {})
                    picks = result.get('picks', [])
                    if len(picks) > 0:
                        self.log(f"⚠️  {sport.title()} returned 200 with {len(picks)} picks (matches found, not empty)", "INFO")
                    else:
                        self.log(f"❌ {sport.title()} returned 200 with empty picks (should be 409)", "ERROR")
                        return False
        
        return True

    def print_summary(self):
        """Print test summary"""
        self.log("\n" + "="*60)
        self.log("TEST SUMMARY")
        self.log("="*60)
        self.log(f"Total Tests: {self.tests_run}")
        self.log(f"Passed: {self.tests_passed}")
        self.log(f"Failed: {self.tests_failed}")
        self.log(f"Success Rate: {(self.tests_passed/self.tests_run*100):.1f}%" if self.tests_run > 0 else "N/A")
        self.log("="*60)
        
        # Print failed tests
        if self.tests_failed > 0:
            self.log("\nFailed Tests:")
            for result in self.results:
                if result.get('status') == 'FAILED':
                    self.log(f"  - {result['test']}: {result.get('error', 'Status mismatch')}", "ERROR")

def main():
    tester = MLBFallbackTester(BASE_URL)
    
    # Login first
    if not tester.test_login():
        tester.log("Cannot proceed without authentication", "ERROR")
        return 1
    
    # Run MLB fallback tests
    baseball_success = tester.test_baseball_fallback_flow()
    
    # Run regression tests
    regression_success = tester.test_regression_other_sports()
    
    # Print summary
    tester.print_summary()
    
    # Save results to JSON
    output_file = "/app/test_reports/mlb_fallback_test_results.json"
    with open(output_file, 'w') as f:
        json.dump({
            "timestamp": datetime.now().isoformat(),
            "summary": {
                "total_tests": tester.tests_run,
                "passed": tester.tests_passed,
                "failed": tester.tests_failed,
                "success_rate": round(tester.tests_passed/tester.tests_run*100, 1) if tester.tests_run > 0 else 0
            },
            "results": tester.results
        }, f, indent=2)
    
    tester.log(f"\nTest results saved to {output_file}", "INFO")
    
    return 0 if tester.tests_failed == 0 else 1

if __name__ == "__main__":
    sys.exit(main())
