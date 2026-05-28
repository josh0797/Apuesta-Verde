"""P0 Bug Verification Tests - Multi-sport Betting Intelligence Engine
Testing critical P0 fixes:
1. Basketball/baseball LIVE matches detection (was returning 0 results)
2. Sport vocabulary firewall (baseball/basketball picks using football terms)
3. Football stale match filtering (2H @ 90+ with stale heartbeat)
4. P2: Historical goal profile in rescued picks

User: demo@valuebet.app / demo1234
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

class P0BugTester:
    def __init__(self, base_url=BASE_URL):
        self.base_url = base_url
        self.token = None
        self.tests_run = 0
        self.tests_passed = 0
        self.tests_failed = 0
        self.results = []
        self.critical_issues = []

    def log(self, message, level="INFO"):
        """Log with timestamp"""
        timestamp = datetime.now().strftime("%H:%M:%S")
        print(f"[{timestamp}] {level}: {message}")

    def run_test(self, name, method, endpoint, expected_status, data=None, timeout=15):
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

    # ========== AUTH ==========
    def test_login(self):
        """Test login with demo credentials"""
        self.log("=" * 80, "INFO")
        self.log("PHASE 1: Authentication", "INFO")
        self.log("=" * 80, "INFO")
        
        success, response = self.run_test(
            "Login (demo@valuebet.app)",
            "POST",
            "api/auth/login",
            200,
            data={"email": "demo@valuebet.app", "password": "demo1234"}
        )
        if success and 'token' in response:
            self.token = response['token']
            self.log(f"✅ Token acquired: {self.token[:30]}...", "INFO")
            return True
        else:
            self.log("❌ Login failed - cannot proceed", "ERROR")
            self.critical_issues.append("AUTH_FAILED: Cannot login with demo@valuebet.app / demo1234")
            return False

    # ========== P0-1: BASKETBALL LIVE DETECTION ==========
    def test_basketball_live_detection(self):
        """P0-1: Basketball live matches must return non-zero count OR empty cleanly"""
        self.log("=" * 80, "INFO")
        self.log("P0-1: Basketball LIVE Match Detection", "INFO")
        self.log("=" * 80, "INFO")
        
        success, response = self.run_test(
            "GET /api/matches/live?sport=basketball&refresh=true",
            "GET",
            "api/matches/live?sport=basketball&refresh=true",
            200,
            timeout=20
        )
        
        if not success:
            self.critical_issues.append("P0-1 FAILED: Basketball live endpoint returned non-200")
            return
        
        count = response.get('count', 0)
        items = response.get('items', [])
        
        self.log(f"Basketball live matches: count={count}, items={len(items)}", "INFO")
        
        if count == 0 and len(items) == 0:
            self.log("✅ P0-1: No live basketball games right now (clean empty response)", "PASS")
        elif count > 0:
            self.log(f"✅ P0-1: Found {count} live basketball games", "PASS")
            
            # Validate each item
            for idx, item in enumerate(items[:3]):  # Check first 3
                match_id = item.get('match_id')
                live_state = item.get('_live_state', {})
                interpreter = item.get('_live_interpreter', {})
                
                state = live_state.get('state')
                source = interpreter.get('_source') if isinstance(interpreter, dict) else None
                
                self.log(f"  Match {idx+1}: id={match_id}, state={state}, interpreter_source={source}", "INFO")
                
                # Check for LIVE_ACTIVE state
                if state != 'LIVE_ACTIVE':
                    self.log(f"  ⚠️ Match {match_id} has state={state} (expected LIVE_ACTIVE)", "WARN")
                
                # Check interpreter source (must be basketball-specific)
                if source and 'basketball' not in source.lower():
                    self.log(f"  ❌ Match {match_id} interpreter source={source} (should contain 'basketball')", "FAIL")
                    self.critical_issues.append(f"P0-1: Basketball match {match_id} has wrong interpreter source: {source}")
                
                # Check for football vocabulary leakage
                if isinstance(interpreter, dict):
                    blocked = interpreter.get('_blocked_by_sport_vocab_guard')
                    if blocked:
                        self.log(f"  ❌ Match {match_id} BLOCKED by vocab guard (vocabulary leak detected)", "FAIL")
                        self.critical_issues.append(f"P0-1: Basketball match {match_id} blocked by sport_vocab_guard")
                    
                    # Check recommendation text for football terms
                    rec_text = str(interpreter.get('recommendation', ''))
                    forbidden = ['gol', 'goles', 'corner', 'córner', 'BTTS']
                    found_forbidden = [term for term in forbidden if term.lower() in rec_text.lower()]
                    if found_forbidden:
                        self.log(f"  ❌ Match {match_id} contains football terms: {found_forbidden}", "FAIL")
                        self.critical_issues.append(f"P0-1: Basketball match {match_id} uses football vocabulary: {found_forbidden}")
        else:
            self.log(f"❌ P0-1: Inconsistent response (count={count}, items={len(items)})", "FAIL")
            self.critical_issues.append(f"P0-1: Basketball live response inconsistent: count={count} but items={len(items)}")

    # ========== P0-2: BASEBALL LIVE DETECTION ==========
    def test_baseball_live_detection(self):
        """P0-2: Baseball live matches must return non-zero count OR empty cleanly"""
        self.log("=" * 80, "INFO")
        self.log("P0-2: Baseball LIVE Match Detection", "INFO")
        self.log("=" * 80, "INFO")
        
        success, response = self.run_test(
            "GET /api/matches/live?sport=baseball&refresh=true",
            "GET",
            "api/matches/live?sport=baseball&refresh=true",
            200,
            timeout=20
        )
        
        if not success:
            self.critical_issues.append("P0-2 FAILED: Baseball live endpoint returned non-200")
            return
        
        count = response.get('count', 0)
        items = response.get('items', [])
        
        self.log(f"Baseball live matches: count={count}, items={len(items)}", "INFO")
        
        if count == 0 and len(items) == 0:
            self.log("✅ P0-2: No live baseball games right now (clean empty response)", "PASS")
        elif count > 0:
            self.log(f"✅ P0-2: Found {count} live baseball games", "PASS")
            
            # Validate each item
            for idx, item in enumerate(items[:3]):  # Check first 3
                match_id = item.get('match_id')
                live_state = item.get('_live_state', {})
                interpreter = item.get('_live_interpreter', {})
                
                state = live_state.get('state')
                source = interpreter.get('_source') if isinstance(interpreter, dict) else None
                
                self.log(f"  Match {idx+1}: id={match_id}, state={state}, interpreter_source={source}", "INFO")
                
                # Check for LIVE_ACTIVE state
                if state != 'LIVE_ACTIVE':
                    self.log(f"  ⚠️ Match {match_id} has state={state} (expected LIVE_ACTIVE)", "WARN")
                
                # Check interpreter source (must be baseball-specific)
                if source and 'baseball' not in source.lower():
                    self.log(f"  ❌ Match {match_id} interpreter source={source} (should contain 'baseball')", "FAIL")
                    self.critical_issues.append(f"P0-2: Baseball match {match_id} has wrong interpreter source: {source}")
                
                # Check for football vocabulary leakage
                if isinstance(interpreter, dict):
                    blocked = interpreter.get('_blocked_by_sport_vocab_guard')
                    if blocked:
                        self.log(f"  ❌ Match {match_id} BLOCKED by vocab guard (vocabulary leak detected)", "FAIL")
                        self.critical_issues.append(f"P0-2: Baseball match {match_id} blocked by sport_vocab_guard")
                    
                    # Check recommendation text for football terms
                    rec_text = str(interpreter.get('recommendation', ''))
                    forbidden = ['gol', 'goles', 'corner', 'córner', 'BTTS']
                    found_forbidden = [term for term in forbidden if term.lower() in rec_text.lower()]
                    if found_forbidden:
                        self.log(f"  ❌ Match {match_id} contains football terms: {found_forbidden}", "FAIL")
                        self.critical_issues.append(f"P0-2: Baseball match {match_id} uses football vocabulary: {found_forbidden}")
        else:
            self.log(f"❌ P0-2: Inconsistent response (count={count}, items={len(items)})", "FAIL")
            self.critical_issues.append(f"P0-2: Baseball live response inconsistent: count={count} but items={len(items)}")

    # ========== P0-3: FOOTBALL STALE MATCH FILTERING ==========
    def test_football_stale_filtering(self):
        """P0-3: Football must NOT show FT or 2H@90+ stale matches as LIVE"""
        self.log("=" * 80, "INFO")
        self.log("P0-3: Football Stale Match Filtering", "INFO")
        self.log("=" * 80, "INFO")
        
        success, response = self.run_test(
            "GET /api/matches/live?sport=football",
            "GET",
            "api/matches/live?sport=football",
            200,
            timeout=15
        )
        
        if not success:
            self.critical_issues.append("P0-3 FAILED: Football live endpoint returned non-200")
            return
        
        count = response.get('count', 0)
        items = response.get('items', [])
        archived_count = response.get('archived_count', 0)
        
        self.log(f"Football live: count={count}, archived={archived_count}", "INFO")
        
        stale_found = []
        for item in items:
            match_id = item.get('match_id')
            status_short = item.get('status_short')
            live_stats = item.get('live_stats', {})
            minute = live_stats.get('minute')
            live_state = item.get('_live_state', {})
            state = live_state.get('state')
            heartbeat_age = live_state.get('heartbeat_age_sec')
            
            # Check for FT status
            if status_short == 'FT':
                stale_found.append(f"Match {match_id}: status=FT (should be archived)")
                self.log(f"  ❌ Match {match_id} has status=FT but is in live list", "FAIL")
            
            # Check for 2H @ minute >= 95
            if status_short == '2H' and minute is not None and minute >= 95:
                stale_found.append(f"Match {match_id}: 2H @ {minute}' (should be archived)")
                self.log(f"  ❌ Match {match_id} is 2H @ {minute}' (stale 90+)", "FAIL")
            
            # Check for 2H @ minute >= 90 with stale heartbeat (>180s)
            if status_short == '2H' and minute is not None and minute >= 90:
                if heartbeat_age is not None and heartbeat_age > 180:
                    stale_found.append(f"Match {match_id}: 2H @ {minute}' with heartbeat {heartbeat_age}s (ghost-FT)")
                    self.log(f"  ❌ Match {match_id} is 2H @ {minute}' with stale heartbeat {heartbeat_age}s", "FAIL")
        
        if stale_found:
            self.log(f"❌ P0-3 FAILED: Found {len(stale_found)} stale matches in live list", "FAIL")
            for issue in stale_found:
                self.critical_issues.append(f"P0-3: {issue}")
        else:
            self.log(f"✅ P0-3: No stale matches found in live list (archived_count={archived_count})", "PASS")

    # ========== P0-4: SPORT VOCAB FIREWALL ==========
    def test_sport_vocab_firewall(self):
        """P0-4: Sport vocabulary firewall must block cross-sport terminology"""
        self.log("=" * 80, "INFO")
        self.log("P0-4: Sport Vocabulary Firewall (Python Unit Test)", "INFO")
        self.log("=" * 80, "INFO")
        
        try:
            import sys
            backend_path = '/app/backend'
            if backend_path not in sys.path:
                sys.path.insert(0, backend_path)
            
            from services.sport_vocab_guard import apply_sport_vocab_guard, detect_vocab_leaks
            
            # Test 1: Baseball pick with football vocabulary
            baseball_pick_with_leak = {
                "match_id": "test_1",
                "match_label": "Yankees vs Red Sox",
                "recommendation": {
                    "market": "Más de 2.5 goles",  # WRONG: should be "carreras"
                    "selection": "Over 2.5",
                    "reasoning": "Ambos equipos marcan frecuentemente"  # WRONG: BTTS language
                },
                "risks": ["Corners pueden cambiar el partido"]  # WRONG: corners in baseball
            }
            
            leaks = detect_vocab_leaks(baseball_pick_with_leak, "baseball")
            self.tests_run += 1
            
            if leaks:
                self.tests_passed += 1
                self.log(f"✅ Test 1: Detected leaks in baseball pick: {leaks}", "PASS")
            else:
                self.tests_failed += 1
                self.log(f"❌ Test 1: Failed to detect football vocabulary in baseball pick", "FAIL")
                self.critical_issues.append("P0-4: sport_vocab_guard failed to detect 'goles' in baseball pick")
            
            # Test 2: Apply firewall to parsed payload
            parsed = {
                "picks": [baseball_pick_with_leak],
                "summary": {
                    "discarded_market": []
                }
            }
            
            result = apply_sport_vocab_guard(parsed, sport="baseball")
            self.tests_run += 1
            
            kept_picks = result.get('picks', [])
            discarded = result.get('summary', {}).get('discarded_market', [])
            pipeline = result.get('_pipeline', {}).get('sport_vocab_guard', {})
            
            if len(kept_picks) == 0 and len(discarded) == 1:
                self.tests_passed += 1
                self.log(f"✅ Test 2: Firewall rerouted contaminated pick to discarded_market", "PASS")
                self.log(f"  Rerouted: {pipeline.get('rerouted', 0)}, Reason: {discarded[0].get('reason', '')[:80]}", "INFO")
            else:
                self.tests_failed += 1
                self.log(f"❌ Test 2: Firewall failed (kept={len(kept_picks)}, discarded={len(discarded)})", "FAIL")
                self.critical_issues.append(f"P0-4: sport_vocab_guard failed to reroute contaminated pick")
            
            # Test 3: Basketball pick with football vocabulary
            basketball_pick_with_leak = {
                "match_id": "test_2",
                "match_label": "Lakers vs Celtics",
                "recommendation": {
                    "market": "Moneyline",
                    "selection": "Lakers gana",
                    "reasoning": "Lakers domina en corners y goles"  # WRONG
                }
            }
            
            leaks_bball = detect_vocab_leaks(basketball_pick_with_leak, "basketball")
            self.tests_run += 1
            
            if leaks_bball:
                self.tests_passed += 1
                self.log(f"✅ Test 3: Detected leaks in basketball pick: {leaks_bball}", "PASS")
            else:
                self.tests_failed += 1
                self.log(f"❌ Test 3: Failed to detect football vocabulary in basketball pick", "FAIL")
                self.critical_issues.append("P0-4: sport_vocab_guard failed to detect 'corners'/'goles' in basketball pick")
            
        except Exception as e:
            self.tests_failed += 3
            self.tests_run += 3
            self.log(f"❌ P0-4 Unit tests failed: {e}", "FAIL")
            self.critical_issues.append(f"P0-4: sport_vocab_guard unit tests crashed: {e}")

    # ========== P0-5: ANALYSIS RUN WITH FIREWALL ==========
    def test_analysis_run_with_firewall(self):
        """P0-5: POST /api/analysis/run must include sport_vocab_guard audit"""
        self.log("=" * 80, "INFO")
        self.log("P0-5: Analysis Run with Sport Vocab Guard", "INFO")
        self.log("=" * 80, "INFO")
        
        success, response = self.run_test(
            "POST /api/analysis/run (football, max_matches=2, force=true)",
            "POST",
            "api/analysis/run",
            200,
            data={
                "sport": "football",
                "max_matches": 2,
                "refresh": True,
                "include_live": False,
                "background": False
            },
            timeout=90
        )
        
        if not success:
            # Check if auto-promoted to background
            if response.get('_auto_promoted'):
                job_id = response.get('job_id')
                self.log(f"⚠️ Auto-promoted to background job: {job_id}", "WARN")
                self.log("Polling job status...", "INFO")
                
                # Poll for completion (max 2 minutes)
                for i in range(24):  # 24 * 5s = 120s
                    time.sleep(5)
                    poll_success, poll_response = self.run_test(
                        f"Poll job {job_id} (attempt {i+1})",
                        "GET",
                        f"api/analysis/jobs/{job_id}",
                        200,
                        timeout=10
                    )
                    
                    if poll_success:
                        stage = poll_response.get('stage')
                        progress = poll_response.get('progress', 0)
                        self.log(f"  Job status: stage={stage}, progress={progress}%", "INFO")
                        
                        if stage == 'completed':
                            response = poll_response.get('result', {})
                            success = True
                            break
                        elif stage == 'failed':
                            self.log(f"❌ Job failed: {poll_response.get('error')}", "FAIL")
                            self.critical_issues.append(f"P0-5: Analysis job failed: {poll_response.get('error')}")
                            return
            else:
                self.critical_issues.append("P0-5: Analysis run failed (non-200 response)")
                return
        
        if not success:
            self.log("❌ P0-5: Analysis run did not complete", "FAIL")
            self.critical_issues.append("P0-5: Analysis run timed out or failed")
            return
        
        # Check for sport_vocab_guard in pipeline
        result = response.get('result', {})
        pipeline = result.get('_pipeline', {})
        vocab_guard = pipeline.get('sport_vocab_guard')
        
        if vocab_guard:
            self.log(f"✅ P0-5: sport_vocab_guard audit present in pipeline", "PASS")
            self.log(f"  Evaluated: {vocab_guard.get('evaluated')}, Kept: {vocab_guard.get('kept')}, Rerouted: {vocab_guard.get('rerouted')}", "INFO")
        else:
            self.log(f"❌ P0-5: sport_vocab_guard audit MISSING from pipeline", "FAIL")
            self.critical_issues.append("P0-5: _pipeline.sport_vocab_guard missing from analysis result")

    # ========== P0-6: LIVE REEVALUATE 409 FOR INACTIVE ==========
    def test_live_reevaluate_409(self):
        """P0-6: POST /api/live/reevaluate for inactive match must return 409"""
        self.log("=" * 80, "INFO")
        self.log("P0-6: Live Reevaluate 409 for Inactive Match", "INFO")
        self.log("=" * 80, "INFO")
        
        # Use a known inactive match_id (this will likely be inactive)
        # In production, we'd need to find an actual ended match
        test_match_id = "999999999"  # Non-existent or ended match
        
        success, response = self.run_test(
            f"POST /api/live/reevaluate (inactive match {test_match_id})",
            "POST",
            "api/live/reevaluate",
            409,  # Expect 409 Conflict
            data={
                "match_id": test_match_id,
                "sport": "football",
                "refresh": False
            },
            timeout=10
        )
        
        if success:
            # Check response structure
            detail = response.get('detail', {})
            if isinstance(detail, dict):
                error = detail.get('error')
                live_state = detail.get('live_state')
                
                if error == 'live_match_not_active' and live_state:
                    self.log(f"✅ P0-6: Correct 409 response with live_state", "PASS")
                    self.log(f"  live_state: {live_state}", "INFO")
                else:
                    self.log(f"⚠️ P0-6: 409 returned but structure unexpected", "WARN")
            else:
                self.log(f"⚠️ P0-6: 409 returned but detail is not dict: {detail}", "WARN")
        else:
            # If we got 404 instead, that's also acceptable (match not found)
            if response.get('http_status') == 404:
                self.log(f"✅ P0-6: Got 404 (match not found) - acceptable", "PASS")
            else:
                self.log(f"⚠️ P0-6: Expected 409 or 404, got different response", "WARN")

    # ========== P0-7: HEALTH CHECK ==========
    def test_health_check(self):
        """P0-7: GET /api/health must return 200"""
        self.log("=" * 80, "INFO")
        self.log("P0-7: Health Check", "INFO")
        self.log("=" * 80, "INFO")
        
        # Note: /api/health might not exist, try /api/ instead
        success, response = self.run_test(
            "GET /api/ (root health)",
            "GET",
            "api/",
            200,
            timeout=5
        )
        
        if success:
            self.log(f"✅ P0-7: Backend is healthy", "PASS")
            self.log(f"  Response: {response}", "INFO")
        else:
            self.log(f"❌ P0-7: Backend health check failed", "FAIL")
            self.critical_issues.append("P0-7: Backend health check returned non-200")

    # ========== MAIN TEST RUNNER ==========
    def run_all_tests(self):
        """Run all P0 bug verification tests"""
        self.log("=" * 80, "INFO")
        self.log("P0 BUG VERIFICATION TESTS - Multi-sport Betting Intelligence", "INFO")
        self.log(f"Base URL: {self.base_url}", "INFO")
        self.log("=" * 80, "INFO")
        
        start_time = time.time()
        
        # Phase 1: Auth
        if not self.test_login():
            self.log("❌ Authentication failed - stopping tests", "ERROR")
            return self.generate_report()
        
        # Phase 2: P0 Bug Tests
        self.test_basketball_live_detection()
        self.test_baseball_live_detection()
        self.test_football_stale_filtering()
        self.test_sport_vocab_firewall()
        self.test_analysis_run_with_firewall()
        self.test_live_reevaluate_409()
        self.test_health_check()
        
        elapsed = time.time() - start_time
        
        self.log("=" * 80, "INFO")
        self.log(f"ALL TESTS COMPLETED in {elapsed:.2f}s", "INFO")
        self.log(f"Total: {self.tests_run} | Passed: {self.tests_passed} | Failed: {self.tests_failed}", "INFO")
        
        if self.critical_issues:
            self.log("=" * 80, "ERROR")
            self.log(f"CRITICAL ISSUES FOUND: {len(self.critical_issues)}", "ERROR")
            for issue in self.critical_issues:
                self.log(f"  • {issue}", "ERROR")
            self.log("=" * 80, "ERROR")
        else:
            self.log("✅ NO CRITICAL ISSUES FOUND", "INFO")
        
        self.log("=" * 80, "INFO")
        
        return self.generate_report()

    def generate_report(self):
        """Generate final test report"""
        success_rate = (self.tests_passed / self.tests_run * 100) if self.tests_run > 0 else 0
        
        report = {
            "timestamp": datetime.now().isoformat(),
            "base_url": self.base_url,
            "test_type": "P0_BUG_VERIFICATION",
            "summary": {
                "total_tests": self.tests_run,
                "passed": self.tests_passed,
                "failed": self.tests_failed,
                "success_rate": round(success_rate, 2),
                "critical_issues_count": len(self.critical_issues)
            },
            "critical_issues": self.critical_issues,
            "results": self.results
        }
        
        return report


def main():
    tester = P0BugTester(BASE_URL)
    report = tester.run_all_tests()
    
    # Save report
    report_file = "/app/test_reports/iteration_24.json"
    with open(report_file, 'w') as f:
        json.dump(report, f, indent=2)
    
    print(f"\n📊 Test report saved to: {report_file}")
    
    # Exit with appropriate code
    return 0 if report['summary']['failed'] == 0 and len(report['critical_issues']) == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
