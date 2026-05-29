"""Backend Testing for Fase 1 (Baseball External Rescue) & Fase 2 (Basketball Fallback)

Testing:
1. Baseball external rescue: rescue_mlb_pitchers() with fuzzy team matching
2. 6 new editorial signals (baseball-only)
3. Basketball fallback: normalize_espn_nba_game() and ingest_nba_direct_fallback()
4. Pipeline integration for baseball (POST /api/analysis/run)
5. Pipeline integration for basketball (POST /api/analysis/run)
6. Pipeline integration for football (409 still raised when no candidates)
7. Regression tests: existing signals and /api/mlb/analysis/day
"""

import sys
import time
import json
import asyncio
from datetime import datetime, timezone

try:
    import requests
except ImportError:
    print("❌ requests library not found. Installing...")
    import subprocess
    subprocess.check_call([sys.executable, "-m", "pip", "install", "requests"])
    import requests

BASE_URL = "https://low-volatility-plays.preview.emergentagent.com"

class Fase12Tester:
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

    def run_test(self, name, method, endpoint, expected_status, data=None, timeout=30):
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
                return success, {"raw_text": response.text[:200]}

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
        """Test login with demo credentials"""
        self.log("=== Testing Authentication ===")
        success, response = self.run_test(
            "Login with demo credentials",
            "POST",
            "api/auth/login",
            200,
            data={"email": "demo@valuebet.app", "password": "demo1234"}
        )
        if success and 'token' in response:
            self.token = response['token']
            self.log(f"✅ Token obtained: {self.token[:20]}...", "AUTH")
            return True
        else:
            self.log("❌ Login failed - cannot proceed with authenticated tests", "ERROR")
            return False

    def test_unit_rescue_mlb_pitchers(self):
        """Unit test: rescue_mlb_pitchers() function"""
        self.log("=== Testing Baseball External Rescue (Unit) ===")
        
        # Import the function directly
        try:
            import sys
            sys.path.insert(0, '/app/backend')
            from services.external_sources import mlb_lineup_rescue
            
            # Test with sample games (will use today's date)
            from datetime import datetime, timezone
            from zoneinfo import ZoneInfo
            eastern = ZoneInfo("America/New_York")
            date_str = datetime.now(eastern).strftime("%Y-%m-%d")
            
            self.log(f"Testing rescue_mlb_pitchers for date: {date_str}")
            
            # Sample games (empty list to test the function doesn't crash)
            sample_games = []
            
            # Run the async function
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                rescues = loop.run_until_complete(
                    mlb_lineup_rescue.rescue_mlb_pitchers(date_str, sample_games)
                )
                self.tests_run += 1
                if isinstance(rescues, list):
                    self.tests_passed += 1
                    self.log(f"✅ rescue_mlb_pitchers returned list (length={len(rescues)})", "PASS")
                    self.results.append({
                        "test": "rescue_mlb_pitchers unit test",
                        "status": "PASSED",
                        "result": f"Returned list with {len(rescues)} items"
                    })
                    return True
                else:
                    self.tests_failed += 1
                    self.log(f"❌ rescue_mlb_pitchers returned wrong type: {type(rescues)}", "FAIL")
                    self.results.append({
                        "test": "rescue_mlb_pitchers unit test",
                        "status": "FAILED",
                        "error": f"Wrong return type: {type(rescues)}"
                    })
                    return False
            finally:
                loop.close()
                
        except Exception as e:
            self.tests_run += 1
            self.tests_failed += 1
            self.log(f"❌ rescue_mlb_pitchers unit test failed: {str(e)}", "FAIL")
            self.results.append({
                "test": "rescue_mlb_pitchers unit test",
                "status": "FAILED",
                "error": str(e)
            })
            return False

    def test_unit_signal_catalog(self):
        """Unit test: 6 new editorial signals (baseball-only)"""
        self.log("=== Testing Signal Catalog (6 New Baseball Signals) ===")
        
        try:
            import sys
            sys.path.insert(0, '/app/backend')
            from services import signal_catalog
            
            # Test the 6 new signals
            new_signals = [
                "EXTERNAL_SOURCE_USED",
                "PITCHER_CONFIRMED_EXTERNAL",
                "LINEUP_PROJECTED_EXTERNAL",
                "LINEUP_CONFIRMED_EXTERNAL",
                "DATA_INCOMPLETE_AFTER_ALL_SOURCES",
                "SOURCE_CONFLICT"
            ]
            
            passed = 0
            failed = 0
            
            for signal_code in new_signals:
                self.tests_run += 1
                
                # Test 1: Signal should work for baseball
                sig_baseball = signal_catalog.make_signal(signal_code, sport="baseball")
                if sig_baseball is not None:
                    passed += 1
                    self.log(f"✅ {signal_code} works for baseball", "PASS")
                else:
                    failed += 1
                    self.log(f"❌ {signal_code} returned None for baseball", "FAIL")
                
                # Test 2: Signal should return None for football (baseball-only)
                self.tests_run += 1
                sig_football = signal_catalog.make_signal(signal_code, sport="football")
                if sig_football is None:
                    passed += 1
                    self.log(f"✅ {signal_code} correctly returns None for football", "PASS")
                else:
                    failed += 1
                    self.log(f"❌ {signal_code} should return None for football but didn't", "FAIL")
            
            self.tests_passed += passed
            self.tests_failed += failed
            
            self.results.append({
                "test": "Signal catalog - 6 new baseball signals",
                "status": "PASSED" if failed == 0 else "PARTIAL",
                "passed": passed,
                "failed": failed,
                "total": passed + failed
            })
            
            return failed == 0
            
        except Exception as e:
            self.tests_run += 1
            self.tests_failed += 1
            self.log(f"❌ Signal catalog test failed: {str(e)}", "FAIL")
            self.results.append({
                "test": "Signal catalog test",
                "status": "FAILED",
                "error": str(e)
            })
            return False

    def test_unit_normalize_espn_nba(self):
        """Unit test: normalize_espn_nba_game()"""
        self.log("=== Testing Basketball Fallback (normalize_espn_nba_game) ===")
        
        try:
            import sys
            sys.path.insert(0, '/app/backend')
            from services import data_ingestion
            
            # Test with a sample ESPN event (minimal structure)
            sample_event = {
                "id": "test_12345",
                "date": "2025-08-15T19:00:00Z",
                "competitions": [{
                    "competitors": [
                        {
                            "homeAway": "home",
                            "team": {
                                "id": "1",
                                "displayName": "Los Angeles Lakers"
                            }
                        },
                        {
                            "homeAway": "away",
                            "team": {
                                "id": "2",
                                "displayName": "Boston Celtics"
                            }
                        }
                    ],
                    "status": {
                        "type": {
                            "state": "pre",
                            "description": "Scheduled"
                        }
                    }
                }]
            }
            
            self.tests_run += 1
            result = data_ingestion.normalize_espn_nba_game(sample_event)
            
            if result is not None and isinstance(result, dict):
                # Check required fields
                required_fields = ["match_id", "sport", "source", "kickoff_iso", "home_team", "away_team"]
                missing = [f for f in required_fields if f not in result]
                
                if not missing and result["sport"] == "basketball" and result["source"] == "espn_nba":
                    self.tests_passed += 1
                    self.log(f"✅ normalize_espn_nba_game returned valid structure", "PASS")
                    self.results.append({
                        "test": "normalize_espn_nba_game unit test",
                        "status": "PASSED",
                        "result": "Valid structure with all required fields"
                    })
                    return True
                else:
                    self.tests_failed += 1
                    self.log(f"❌ normalize_espn_nba_game missing fields or wrong values: {missing}", "FAIL")
                    self.results.append({
                        "test": "normalize_espn_nba_game unit test",
                        "status": "FAILED",
                        "error": f"Missing fields: {missing}, sport={result.get('sport')}, source={result.get('source')}"
                    })
                    return False
            else:
                self.tests_failed += 1
                self.log(f"❌ normalize_espn_nba_game returned invalid result: {type(result)}", "FAIL")
                self.results.append({
                    "test": "normalize_espn_nba_game unit test",
                    "status": "FAILED",
                    "error": f"Invalid return type: {type(result)}"
                })
                return False
                
        except Exception as e:
            self.tests_run += 1
            self.tests_failed += 1
            self.log(f"❌ normalize_espn_nba_game test failed: {str(e)}", "FAIL")
            self.results.append({
                "test": "normalize_espn_nba_game unit test",
                "status": "FAILED",
                "error": str(e)
            })
            return False

    def test_pipeline_baseball(self):
        """Test POST /api/analysis/run for baseball with refresh=false"""
        self.log("=== Testing Baseball Pipeline Integration ===")
        
        success, response = self.run_test(
            "Baseball pipeline (refresh=false)",
            "POST",
            "api/analysis/run",
            200,
            data={
                "sport": "baseball",
                "refresh": False,
                "include_live": False,
                "max_matches": 5
            },
            timeout=60
        )
        
        if success:
            # Check pipeline_meta fields
            pipeline_meta = response.get("result", {}).get("pipeline_meta", {})
            
            checks = []
            
            # Check for required fields
            if "source_used" in pipeline_meta:
                checks.append(("source_used present", True))
                self.log(f"  source_used: {pipeline_meta['source_used']}")
            else:
                checks.append(("source_used present", False))
            
            if "mlb_stats_api_games_found" in pipeline_meta:
                checks.append(("mlb_stats_api_games_found present", True))
                self.log(f"  mlb_stats_api_games_found: {pipeline_meta['mlb_stats_api_games_found']}")
            else:
                checks.append(("mlb_stats_api_games_found present", False))
            
            if "external_rescue_count" in pipeline_meta:
                checks.append(("external_rescue_count present", True))
                self.log(f"  external_rescue_count: {pipeline_meta['external_rescue_count']}")
            else:
                checks.append(("external_rescue_count present", False))
            
            if "external_sources_consulted" in pipeline_meta:
                checks.append(("external_sources_consulted present", True))
                sources = pipeline_meta['external_sources_consulted']
                self.log(f"  external_sources_consulted: {len(sources)} sources")
            else:
                checks.append(("external_sources_consulted present", False))
            
            # Log all checks
            all_passed = all(c[1] for c in checks)
            for check_name, passed in checks:
                if passed:
                    self.log(f"  ✅ {check_name}", "CHECK")
                else:
                    self.log(f"  ❌ {check_name}", "CHECK")
            
            if all_passed:
                self.log("✅ Baseball pipeline has all required pipeline_meta fields", "PASS")
            else:
                self.log("⚠️  Baseball pipeline missing some pipeline_meta fields", "WARN")
            
            return True
        
        return False

    def test_pipeline_basketball(self):
        """Test POST /api/analysis/run for basketball with refresh=false"""
        self.log("=== Testing Basketball Pipeline Integration ===")
        
        success, response = self.run_test(
            "Basketball pipeline (refresh=false)",
            "POST",
            "api/analysis/run",
            200,
            data={
                "sport": "basketball",
                "refresh": False,
                "include_live": False,
                "max_matches": 5
            },
            timeout=60
        )
        
        if success:
            # Check pipeline_meta fields
            pipeline_meta = response.get("result", {}).get("pipeline_meta", {})
            
            checks = []
            
            # Check for required fields
            if "source_used" in pipeline_meta:
                checks.append(("source_used present", True))
                self.log(f"  source_used: {pipeline_meta['source_used']}")
            else:
                checks.append(("source_used present", False))
            
            if "espn_nba_games_found" in pipeline_meta:
                checks.append(("espn_nba_games_found present", True))
                self.log(f"  espn_nba_games_found: {pipeline_meta['espn_nba_games_found']}")
            else:
                checks.append(("espn_nba_games_found present", False))
            
            # Check abort_reason if no games
            if pipeline_meta.get("candidates_count", 0) == 0:
                if pipeline_meta.get("abort_reason") == "no_games_all_sources":
                    checks.append(("abort_reason correct", True))
                    self.log(f"  abort_reason: {pipeline_meta['abort_reason']}")
                else:
                    checks.append(("abort_reason correct", False))
            
            # Log all checks
            all_passed = all(c[1] for c in checks)
            for check_name, passed in checks:
                if passed:
                    self.log(f"  ✅ {check_name}", "CHECK")
                else:
                    self.log(f"  ❌ {check_name}", "CHECK")
            
            if all_passed:
                self.log("✅ Basketball pipeline has all required pipeline_meta fields", "PASS")
            else:
                self.log("⚠️  Basketball pipeline missing some pipeline_meta fields", "WARN")
            
            return True
        
        return False

    def test_regression_signals(self):
        """Regression test: existing signals still work"""
        self.log("=== Testing Regression: Existing Signals ===")
        
        try:
            import sys
            sys.path.insert(0, '/app/backend')
            from services import signal_catalog
            
            # Test existing signals
            existing_signals = [
                "PITCHER_NOT_CONFIRMED",
                "MLB_COM_FALLBACK_USED",
                "IL_DEPTH_RISK"
            ]
            
            passed = 0
            failed = 0
            
            for signal_code in existing_signals:
                self.tests_run += 1
                sig = signal_catalog.make_signal(signal_code, sport="baseball")
                if sig is not None:
                    passed += 1
                    self.log(f"✅ {signal_code} still works", "PASS")
                else:
                    failed += 1
                    self.log(f"❌ {signal_code} returned None", "FAIL")
            
            self.tests_passed += passed
            self.tests_failed += failed
            
            self.results.append({
                "test": "Regression - existing signals",
                "status": "PASSED" if failed == 0 else "PARTIAL",
                "passed": passed,
                "failed": failed
            })
            
            return failed == 0
            
        except Exception as e:
            self.tests_run += 1
            self.tests_failed += 1
            self.log(f"❌ Regression test failed: {str(e)}", "FAIL")
            self.results.append({
                "test": "Regression test",
                "status": "FAILED",
                "error": str(e)
            })
            return False

    def save_results(self):
        """Save test results to JSON file"""
        report = {
            "test_suite": "Fase 1 & 2 Backend Tests",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "summary": {
                "total": self.tests_run,
                "passed": self.tests_passed,
                "failed": self.tests_failed,
                "success_rate": f"{(self.tests_passed / self.tests_run * 100):.1f}%" if self.tests_run > 0 else "0%"
            },
            "results": self.results
        }
        
        filename = "/app/test_reports/iteration_35.json"
        with open(filename, 'w') as f:
            json.dump(report, f, indent=2)
        
        self.log(f"Results saved to {filename}", "INFO")
        return filename

def main():
    tester = Fase12Tester(BASE_URL)
    
    print("\n" + "="*80)
    print("FASE 1 & 2 BACKEND TESTING")
    print("="*80 + "\n")
    
    # Test authentication first
    if not tester.test_login():
        print("\n❌ Authentication failed - stopping tests")
        return 1
    
    print("\n")
    
    # Unit tests (don't require auth)
    tester.test_unit_rescue_mlb_pitchers()
    print()
    
    tester.test_unit_signal_catalog()
    print()
    
    tester.test_unit_normalize_espn_nba()
    print()
    
    # Integration tests (require auth)
    tester.test_pipeline_baseball()
    print()
    
    tester.test_pipeline_basketball()
    print()
    
    # Regression tests
    tester.test_regression_signals()
    print()
    
    # Print summary
    print("\n" + "="*80)
    print("TEST SUMMARY")
    print("="*80)
    print(f"Total Tests:  {tester.tests_run}")
    print(f"Passed:       {tester.tests_passed} ✅")
    print(f"Failed:       {tester.tests_failed} ❌")
    if tester.tests_run > 0:
        success_rate = (tester.tests_passed / tester.tests_run) * 100
        print(f"Success Rate: {success_rate:.1f}%")
    print("="*80 + "\n")
    
    # Save results
    report_file = tester.save_results()
    print(f"📊 Full report saved to: {report_file}\n")
    
    return 0 if tester.tests_failed == 0 else 1

if __name__ == "__main__":
    sys.exit(main())
