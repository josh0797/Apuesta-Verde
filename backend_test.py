"""Backend API Testing for Value Bet Intelligence
Tests all endpoints as specified in the review request.
"""
import requests
import sys
import time
from datetime import datetime

class ValueBetAPITester:
    def __init__(self, base_url="https://low-volatility-plays.preview.emergentagent.com"):
        self.base_url = base_url
        self.token = None
        self.tests_run = 0
        self.tests_passed = 0
        self.tests_failed = []
        self.demo_user = {"email": "demo@valuebet.app", "password": "demo1234"}
        
    def log(self, message, level="INFO"):
        """Log test messages"""
        timestamp = datetime.now().strftime("%H:%M:%S")
        print(f"[{timestamp}] {level}: {message}")
    
    def run_test(self, name, method, endpoint, expected_status, data=None, headers=None, timeout=120):
        """Run a single API test"""
        url = f"{self.base_url}/api/{endpoint}"
        test_headers = {'Content-Type': 'application/json'}
        
        if self.token and not (headers and 'skip_auth' in headers):
            test_headers['Authorization'] = f'Bearer {self.token}'
        
        if headers:
            headers.pop('skip_auth', None)
            test_headers.update(headers)
        
        self.tests_run += 1
        self.log(f"Testing {name}...")
        
        try:
            if method == 'GET':
                response = requests.get(url, headers=test_headers, timeout=timeout)
            elif method == 'POST':
                response = requests.post(url, json=data, headers=test_headers, timeout=timeout)
            elif method == 'PATCH':
                response = requests.patch(url, json=data, headers=test_headers, timeout=timeout)
            
            success = response.status_code == expected_status
            
            if success:
                self.tests_passed += 1
                self.log(f"✅ PASSED - {name} (Status: {response.status_code})", "PASS")
                try:
                    return True, response.json()
                except:
                    return True, {}
            else:
                self.tests_failed.append({
                    "test": name,
                    "expected": expected_status,
                    "got": response.status_code,
                    "response": response.text[:200]
                })
                self.log(f"❌ FAILED - {name} (Expected {expected_status}, got {response.status_code})", "FAIL")
                self.log(f"   Response: {response.text[:200]}", "FAIL")
                return False, {}
                
        except requests.exceptions.Timeout:
            self.tests_failed.append({
                "test": name,
                "expected": expected_status,
                "got": "TIMEOUT",
                "response": f"Request timed out after {timeout}s"
            })
            self.log(f"❌ FAILED - {name} (Timeout after {timeout}s)", "FAIL")
            return False, {}
        except Exception as e:
            self.tests_failed.append({
                "test": name,
                "expected": expected_status,
                "got": "ERROR",
                "response": str(e)
            })
            self.log(f"❌ FAILED - {name} (Error: {str(e)})", "FAIL")
            return False, {}
    
    def test_public_health(self):
        """Test public health endpoint (no auth required)"""
        self.log("\n=== Testing Public Health Endpoint ===")
        success, response = self.run_test(
            "Public Health Check",
            "GET",
            "",
            200,
            headers={'skip_auth': True}
        )
        if success:
            self.log(f"   App: {response.get('app')}, Status: {response.get('status')}")
        return success
    
    def test_demo_login(self):
        """Test login with demo credentials"""
        self.log("\n=== Testing Demo User Login ===")
        success, response = self.run_test(
            "Demo Login",
            "POST",
            "auth/login",
            200,
            data=self.demo_user,
            headers={'skip_auth': True}
        )
        if success and 'token' in response:
            self.token = response['token']
            self.log(f"   Token obtained: {self.token[:20]}...")
            self.log(f"   User: {response.get('user', {}).get('email')}")
            return True
        return False
    
    def test_register_new_user(self):
        """Test registering a new user"""
        self.log("\n=== Testing User Registration ===")
        timestamp = int(time.time())
        new_user = {
            "email": f"test_{timestamp}@valuebet.com",
            "password": "testpass123",
            "name": f"Test User {timestamp}"
        }
        success, response = self.run_test(
            "Register New User",
            "POST",
            "auth/register",
            200,
            data=new_user,
            headers={'skip_auth': True}
        )
        if success:
            self.log(f"   New user created: {response.get('user', {}).get('email')}")
            # Test login with new user
            success2, response2 = self.run_test(
                "Login with New User",
                "POST",
                "auth/login",
                200,
                data={"email": new_user["email"], "password": new_user["password"]},
                headers={'skip_auth': True}
            )
            if success2:
                # Restore demo token
                self.test_demo_login()
            return success2
        return False
    
    def test_auth_me(self):
        """Test /api/auth/me endpoint"""
        self.log("\n=== Testing Auth Me Endpoint ===")
        success, response = self.run_test(
            "Get Current User",
            "GET",
            "auth/me",
            200
        )
        if success:
            self.log(f"   User ID: {response.get('id')}")
            self.log(f"   Email: {response.get('email')}")
            self.log(f"   Language: {response.get('language')}")
        return success
    
    def test_unauthorized_access(self):
        """Test that endpoints require auth"""
        self.log("\n=== Testing Authorization (401 without token) ===")
        saved_token = self.token
        self.token = None
        
        success, _ = self.run_test(
            "Unauthorized Access to /matches/upcoming",
            "GET",
            "matches/upcoming",
            401,
            headers={'skip_auth': True}
        )
        
        self.token = saved_token
        return success
    
    def test_matches_upcoming(self):
        """Test GET /api/matches/upcoming"""
        self.log("\n=== Testing Matches Upcoming ===")
        success, response = self.run_test(
            "Get Upcoming Matches",
            "GET",
            "matches/upcoming",
            200
        )
        if success:
            count = response.get('count', 0)
            items = response.get('items', [])
            self.log(f"   Found {count} upcoming matches")
            if items:
                first = items[0]
                self.log(f"   Sample: {first.get('home_team', {}).get('name')} vs {first.get('away_team', {}).get('name')}")
                self.log(f"   Match ID: {first.get('match_id')}")
                self.log(f"   League: {first.get('league')}")
                # Store a match_id for later tests
                self.sample_match_id = first.get('match_id')
        return success
    
    def test_matches_live(self):
        """Test GET /api/matches/live"""
        self.log("\n=== Testing Matches Live ===")
        success, response = self.run_test(
            "Get Live Matches",
            "GET",
            "matches/live",
            200
        )
        if success:
            count = response.get('count', 0)
            self.log(f"   Found {count} live matches (may be 0 if no games live)")
        return success
    
    def test_match_detail(self):
        """Test GET /api/matches/{id}"""
        self.log("\n=== Testing Match Detail ===")
        # Use known match ID from review request (Chelsea-Tottenham)
        match_id = 1379333
        success, response = self.run_test(
            f"Get Match Detail (ID: {match_id})",
            "GET",
            f"matches/{match_id}",
            200
        )
        if success:
            self.log(f"   Match: {response.get('home_team', {}).get('name')} vs {response.get('away_team', {}).get('name')}")
            self.log(f"   League: {response.get('league')}")
            self.log(f"   Kickoff: {response.get('kickoff_iso')}")
            self.log(f"   Data complete: {response.get('data_complete')}")
            odds_history = response.get('odds_history', [])
            self.log(f"   Odds history snapshots: {len(odds_history)}")
        return success
    
    def test_analysis_run(self):
        """Test POST /api/analysis/run"""
        self.log("\n=== Testing Analysis Run ===")
        self.log("   Note: This may take 20-90 seconds (LLM processing)...")
        
        payload = {
            "refresh": False,  # Use cached data as instructed
            "include_live": False,
            "max_matches": 6
        }
        
        success, response = self.run_test(
            "Run Analysis (refresh=false)",
            "POST",
            "analysis/run",
            200,
            data=payload,
            timeout=120  # Allow up to 120s for LLM
        )
        
        if success:
            self.log(f"   Pick Run ID: {response.get('pick_run_id')}")
            self.log(f"   Generated at: {response.get('generated_at')}")
            result = response.get('result', {})
            self.log(f"   Verdict: {result.get('verdict')}")
            
            summary = result.get('summary', {})
            self.log(f"   Total analyzed: {summary.get('total_analyzed')}")
            self.log(f"   Total recommended: {summary.get('total_recommended')}")
            self.log(f"   Total discarded: {summary.get('total_discarded')}")
            
            picks = result.get('picks', [])
            self.log(f"   Picks returned: {len(picks)}")
            
            if picks:
                first_pick = picks[0]
                self.log(f"   Sample pick: {first_pick.get('match_label')}")
                self.log(f"   Market: {first_pick.get('recommendation', {}).get('market')}")
                self.log(f"   Confidence: {first_pick.get('recommendation', {}).get('confidence_score')}")
                
                # Store for tracking test
                self.sample_run_id = response.get('pick_run_id')
                self.sample_pick_match_id = first_pick.get('match_id')
            
        return success
    
    def test_picks_today(self):
        """Test GET /api/picks/today"""
        self.log("\n=== Testing Picks Today ===")
        success, response = self.run_test(
            "Get Today's Picks",
            "GET",
            "picks/today",
            200
        )
        if success:
            pick_run = response.get('pick_run')
            if pick_run:
                self.log(f"   Pick Run ID: {pick_run.get('id')}")
                self.log(f"   Generated at: {pick_run.get('generated_at')}")
                self.log(f"   Matches analyzed: {pick_run.get('matches_analyzed')}")
            else:
                self.log("   No pick run found (user may not have run analysis yet)")
        return success
    
    def test_picks_history(self):
        """Test GET /api/picks/history"""
        self.log("\n=== Testing Picks History ===")
        success, response = self.run_test(
            "Get Picks History",
            "GET",
            "picks/history",
            200
        )
        if success:
            count = response.get('count', 0)
            items = response.get('items', [])
            self.log(f"   Found {count} historical pick runs")
            if items:
                latest = items[0]
                self.log(f"   Latest run ID: {latest.get('id')}")
                self.log(f"   Verdict: {latest.get('verdict')}")
                self.log(f"   Recommended: {latest.get('total_recommended')}")
        return success
    
    def test_pick_tracking(self):
        """Test POST /api/picks/track"""
        self.log("\n=== Testing Pick Tracking ===")
        
        # First get a pick run to track
        success, response = self.run_test(
            "Get Pick Run for Tracking",
            "GET",
            "picks/today",
            200
        )
        
        if not success or not response.get('pick_run'):
            self.log("   Skipping tracking test - no pick run available")
            return True  # Not a failure, just no data
        
        pick_run = response['pick_run']
        run_id = pick_run.get('id')
        picks = pick_run.get('payload', {}).get('picks', [])
        
        if not picks:
            self.log("   Skipping tracking test - no picks in run")
            return True
        
        first_pick = picks[0]
        match_id = first_pick.get('match_id')
        
        track_payload = {
            "run_id": run_id,
            "match_id": match_id,
            "market": first_pick.get('recommendation', {}).get('market', 'Under 2.5'),
            "selection": first_pick.get('recommendation', {}).get('selection', 'Under 2.5'),
            "confidence_score": first_pick.get('recommendation', {}).get('confidence_score', 75),
            "outcome": "pending",
            "odds": 1.75,
            "league": first_pick.get('league'),
            "match_label": first_pick.get('match_label'),
            "notes": "Test tracking"
        }
        
        success, response = self.run_test(
            "Track Pick (pending)",
            "POST",
            "picks/track",
            200,
            data=track_payload
        )
        
        if success:
            self.log(f"   Pick tracked: {response.get('pick_id')}")
            self.log(f"   Outcome: {response.get('outcome')}")
            
            # Test idempotency - track same pick again with different outcome
            track_payload['outcome'] = 'won'
            success2, response2 = self.run_test(
                "Track Pick (won - idempotent update)",
                "POST",
                "picks/track",
                200,
                data=track_payload
            )
            if success2:
                self.log(f"   Updated outcome: {response2.get('outcome')}")
            return success2
        
        return success
    
    def test_tracked_picks(self):
        """Test GET /api/picks/tracked"""
        self.log("\n=== Testing Tracked Picks List ===")
        success, response = self.run_test(
            "Get Tracked Picks",
            "GET",
            "picks/tracked",
            200
        )
        if success:
            count = response.get('count', 0)
            items = response.get('items', [])
            self.log(f"   Found {count} tracked picks")
            if items:
                latest = items[0]
                self.log(f"   Latest: {latest.get('match_label')}")
                self.log(f"   Outcome: {latest.get('outcome')}")
                self.log(f"   Confidence: {latest.get('confidence_score')}")
        return success
    
    def test_stats_dashboard(self):
        """Test GET /api/stats/dashboard"""
        self.log("\n=== Testing Stats Dashboard ===")
        success, response = self.run_test(
            "Get Stats Dashboard",
            "GET",
            "stats/dashboard",
            200
        )
        if success:
            self.log(f"   Total picks: {response.get('total')}")
            self.log(f"   Won: {response.get('won')}")
            self.log(f"   Lost: {response.get('lost')}")
            self.log(f"   Push: {response.get('push')}")
            self.log(f"   Pending: {response.get('pending')}")
            self.log(f"   Win rate: {response.get('win_rate')}%")
            self.log(f"   Current streak: {response.get('streak')}")
            
            accuracy = response.get('accuracy_by_tier', {})
            for tier, data in accuracy.items():
                self.log(f"   {tier}: {data.get('won')}/{data.get('settled')} ({data.get('rate')}%)")
        return success
    
    def test_language_update(self):
        """Test PATCH /api/auth/me/language"""
        self.log("\n=== Testing Language Update ===")
        
        # Update to English
        success, response = self.run_test(
            "Update Language to EN",
            "PATCH",
            "auth/me/language",
            200,
            data={"language": "en"}
        )
        if success:
            self.log(f"   Language updated: {response.get('language')}")
            
            # Update back to Spanish
            success2, response2 = self.run_test(
                "Update Language to ES",
                "PATCH",
                "auth/me/language",
                200,
                data={"language": "es"}
            )
            if success2:
                self.log(f"   Language restored: {response2.get('language')}")
            return success2
        
        return success
    
    def run_all_tests(self):
        """Run all tests in sequence"""
        self.log("\n" + "="*70)
        self.log("VALUE BET INTELLIGENCE - BACKEND API TEST SUITE")
        self.log("="*70)
        
        start_time = time.time()
        
        # Test sequence
        tests = [
            ("Public Health", self.test_public_health),
            ("Demo Login", self.test_demo_login),
            ("User Registration", self.test_register_new_user),
            ("Auth Me", self.test_auth_me),
            ("Unauthorized Access", self.test_unauthorized_access),
            ("Matches Upcoming", self.test_matches_upcoming),
            ("Matches Live", self.test_matches_live),
            ("Match Detail", self.test_match_detail),
            ("Analysis Run", self.test_analysis_run),
            ("Picks Today", self.test_picks_today),
            ("Picks History", self.test_picks_history),
            ("Pick Tracking", self.test_pick_tracking),
            ("Tracked Picks", self.test_tracked_picks),
            ("Stats Dashboard", self.test_stats_dashboard),
            ("Language Update", self.test_language_update),
        ]
        
        for test_name, test_func in tests:
            try:
                test_func()
            except Exception as e:
                self.log(f"❌ Test '{test_name}' crashed: {str(e)}", "ERROR")
                self.tests_failed.append({
                    "test": test_name,
                    "expected": "success",
                    "got": "CRASH",
                    "response": str(e)
                })
        
        elapsed = time.time() - start_time
        
        # Print summary
        self.log("\n" + "="*70)
        self.log("TEST SUMMARY")
        self.log("="*70)
        self.log(f"Total tests run: {self.tests_run}")
        self.log(f"Tests passed: {self.tests_passed}")
        self.log(f"Tests failed: {len(self.tests_failed)}")
        self.log(f"Success rate: {(self.tests_passed/self.tests_run*100):.1f}%")
        self.log(f"Time elapsed: {elapsed:.1f}s")
        
        if self.tests_failed:
            self.log("\n" + "="*70)
            self.log("FAILED TESTS DETAILS")
            self.log("="*70)
            for failure in self.tests_failed:
                self.log(f"\n❌ {failure['test']}")
                self.log(f"   Expected: {failure['expected']}")
                self.log(f"   Got: {failure['got']}")
                self.log(f"   Response: {failure['response']}")
        
        self.log("\n" + "="*70)
        
        return 0 if len(self.tests_failed) == 0 else 1

def main():
    tester = ValueBetAPITester()
    return tester.run_all_tests()

if __name__ == "__main__":
    sys.exit(main())
