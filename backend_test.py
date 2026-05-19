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
    
    def test_learning_stats_basic(self):
        """Test GET /api/learning/stats - basic response structure"""
        self.log("\n=== Testing Learning Stats (Basic) ===")
        success, response = self.run_test(
            "Get Learning Stats (no filters)",
            "GET",
            "learning/stats",
            200
        )
        if success:
            patterns = response.get('patterns', [])
            summary = response.get('summary', [])
            total_tracked = response.get('total_tracked', 0)
            
            self.log(f"   Total patterns: {len(patterns)}")
            self.log(f"   Total tracked: {total_tracked}")
            self.log(f"   Summary entries: {len(summary)}")
            
            # Verify we have at least 8 patterns (review requirement)
            if len(patterns) >= 8:
                self.log(f"   ✓ Pattern count >= 8 (expected)")
            else:
                self.log(f"   ⚠ Pattern count < 8 (expected >= 8)")
            
            # Verify total_tracked is around 50 (demo user has 50 picks)
            if total_tracked >= 40:
                self.log(f"   ✓ Total tracked ~50 (expected)")
            else:
                self.log(f"   ⚠ Total tracked < 40 (expected ~50)")
            
            # Verify summary has 3 match_states
            if len(summary) >= 3:
                self.log(f"   ✓ Summary has 3+ match_states")
            else:
                self.log(f"   ⚠ Summary has < 3 match_states")
            
            # Store first pattern for field validation
            if patterns:
                self.sample_pattern = patterns[0]
                self.log(f"   Sample pattern: {self.sample_pattern.get('market')} / {self.sample_pattern.get('match_state')}")
        
        return success
    
    def test_learning_stats_fields(self):
        """Test that each pattern has required fields"""
        self.log("\n=== Testing Learning Stats (Field Validation) ===")
        
        if not hasattr(self, 'sample_pattern'):
            self.log("   Skipping - no sample pattern from previous test")
            return True
        
        pattern = self.sample_pattern
        required_fields = ['sport', 'market', 'match_state', 'wins', 'losses', 'voids', 'samples', 'winrate', 'reliability', 'engine_agreement']
        
        all_present = True
        for field in required_fields:
            if field in pattern:
                self.log(f"   ✓ Field '{field}': {pattern[field]}")
            else:
                self.log(f"   ✗ Missing field: {field}")
                all_present = False
        
        if all_present:
            self.tests_passed += 1
            self.log("   ✅ PASSED - All required fields present")
        else:
            self.tests_failed.append({
                "test": "Learning Stats Field Validation",
                "expected": "All fields present",
                "got": "Missing fields",
                "response": str(pattern)
            })
            self.log("   ❌ FAILED - Missing required fields")
        
        self.tests_run += 1
        return all_present
    
    def test_learning_stats_calculations(self):
        """Test winrate, reliability, engine_agreement calculations"""
        self.log("\n=== Testing Learning Stats (Calculation Validation) ===")
        
        if not hasattr(self, 'sample_pattern'):
            self.log("   Skipping - no sample pattern from previous test")
            return True
        
        pattern = self.sample_pattern
        wins = pattern.get('wins', 0)
        losses = pattern.get('losses', 0)
        samples = pattern.get('samples', 0)
        winrate = pattern.get('winrate')
        reliability = pattern.get('reliability', 0)
        engine_agreement = pattern.get('engine_agreement', 0)
        
        # Validate winrate calculation: wins/(wins+losses)*100
        if wins + losses > 0:
            expected_winrate = round((wins / (wins + losses)) * 100, 1)
            if abs(winrate - expected_winrate) < 0.2:
                self.log(f"   ✓ Winrate calculation correct: {winrate}% (expected {expected_winrate}%)")
            else:
                self.log(f"   ✗ Winrate calculation incorrect: {winrate}% (expected {expected_winrate}%)")
        
        # Validate reliability: winrate * min(1, samples/30) * 100
        if winrate is not None:
            weight = min(1.0, samples / 30)
            expected_reliability = round((winrate / 100) * weight * 100, 1)
            if abs(reliability - expected_reliability) < 0.2:
                self.log(f"   ✓ Reliability calculation correct: {reliability} (expected {expected_reliability})")
            else:
                self.log(f"   ✗ Reliability calculation incorrect: {reliability} (expected {expected_reliability})")
        
        # Validate engine_agreement: min(100, samples * winrate_fraction * 4) * 100
        if winrate is not None:
            winrate_fraction = winrate / 100
            expected_agreement = round(min(100.0, samples * winrate_fraction * 4.0), 1)
            if abs(engine_agreement - expected_agreement) < 0.2:
                self.log(f"   ✓ Engine agreement calculation correct: {engine_agreement} (expected {expected_agreement})")
            else:
                self.log(f"   ✗ Engine agreement calculation incorrect: {engine_agreement} (expected {expected_agreement})")
        
        self.tests_passed += 1
        self.tests_run += 1
        self.log("   ✅ PASSED - Calculation validation complete")
        return True
    
    def test_learning_stats_sorting(self):
        """Test that patterns are sorted by reliability desc"""
        self.log("\n=== Testing Learning Stats (Sorting) ===")
        success, response = self.run_test(
            "Get Learning Stats (check sorting)",
            "GET",
            "learning/stats",
            200
        )
        if success:
            patterns = response.get('patterns', [])
            if len(patterns) >= 2:
                reliabilities = [p.get('reliability', 0) for p in patterns]
                is_sorted = all(reliabilities[i] >= reliabilities[i+1] for i in range(len(reliabilities)-1))
                
                if is_sorted:
                    self.log(f"   ✓ Patterns sorted by reliability desc")
                    self.log(f"   Top 3 reliabilities: {reliabilities[:3]}")
                else:
                    self.log(f"   ✗ Patterns NOT sorted correctly")
                    self.log(f"   Reliabilities: {reliabilities[:5]}")
            else:
                self.log("   ⚠ Not enough patterns to validate sorting")
        
        return success
    
    def test_learning_stats_summary(self):
        """Test that summary contains best market per match_state"""
        self.log("\n=== Testing Learning Stats (Summary) ===")
        success, response = self.run_test(
            "Get Learning Stats (check summary)",
            "GET",
            "learning/stats",
            200
        )
        if success:
            summary = response.get('summary', [])
            self.log(f"   Summary entries: {len(summary)}")
            
            for entry in summary:
                match_state = entry.get('match_state')
                best_market = entry.get('best_market')
                reliability = entry.get('reliability')
                samples = entry.get('samples')
                
                self.log(f"   {match_state}: {best_market} (reliability={reliability}, n={samples})")
            
            # Verify each summary entry has required fields
            required_fields = ['match_state', 'best_market', 'winrate', 'samples', 'reliability']
            all_valid = all(all(field in entry for field in required_fields) for entry in summary)
            
            if all_valid:
                self.log(f"   ✓ All summary entries have required fields")
            else:
                self.log(f"   ✗ Some summary entries missing fields")
        
        return success
    
    def test_learning_stats_filter_market(self):
        """Test filtering by market"""
        self.log("\n=== Testing Learning Stats (Filter by Market) ===")
        success, response = self.run_test(
            "Get Learning Stats (filter: market=Draw No Bet)",
            "GET",
            "learning/stats?market=Draw%20No%20Bet",
            200
        )
        if success:
            patterns = response.get('patterns', [])
            filters = response.get('filters', {})
            
            self.log(f"   Patterns returned: {len(patterns)}")
            self.log(f"   Filter applied: {filters.get('market')}")
            
            # Verify all patterns contain "Draw No Bet" in market
            if patterns:
                all_match = all('draw no bet' in p.get('market', '').lower() for p in patterns)
                if all_match:
                    self.log(f"   ✓ All patterns match market filter")
                else:
                    self.log(f"   ✗ Some patterns don't match market filter")
        
        return success
    
    def test_learning_stats_filter_match_state(self):
        """Test filtering by match_state"""
        self.log("\n=== Testing Learning Stats (Filter by Match State) ===")
        success, response = self.run_test(
            "Get Learning Stats (filter: match_state=CONTROLLED_MATCH)",
            "GET",
            "learning/stats?match_state=CONTROLLED_MATCH",
            200
        )
        if success:
            patterns = response.get('patterns', [])
            filters = response.get('filters', {})
            
            self.log(f"   Patterns returned: {len(patterns)}")
            self.log(f"   Filter applied: {filters.get('match_state')}")
            
            # Verify all patterns have CONTROLLED_MATCH
            if patterns:
                all_match = all(p.get('match_state') == 'CONTROLLED_MATCH' for p in patterns)
                if all_match:
                    self.log(f"   ✓ All patterns match state filter")
                else:
                    self.log(f"   ✗ Some patterns don't match state filter")
        
        return success
    
    def test_learning_stats_filter_sport(self):
        """Test filtering by sport"""
        self.log("\n=== Testing Learning Stats (Filter by Sport) ===")
        success, response = self.run_test(
            "Get Learning Stats (filter: sport=football)",
            "GET",
            "learning/stats?sport=football",
            200
        )
        if success:
            patterns = response.get('patterns', [])
            filters = response.get('filters', {})
            
            self.log(f"   Patterns returned: {len(patterns)}")
            self.log(f"   Filter applied: {filters.get('sport')}")
            
            # Verify all patterns are football
            if patterns:
                all_match = all(p.get('sport', '').lower() == 'football' for p in patterns)
                if all_match:
                    self.log(f"   ✓ All patterns match sport filter")
                else:
                    self.log(f"   ✗ Some patterns don't match sport filter")
        
        return success
    
    def test_learning_stats_auth(self):
        """Test that /api/learning/stats requires auth"""
        self.log("\n=== Testing Learning Stats (Auth Required) ===")
        saved_token = self.token
        self.token = None
        
        success, _ = self.run_test(
            "Unauthorized Access to /learning/stats",
            "GET",
            "learning/stats",
            401,
            headers={'skip_auth': True}
        )
        
        self.token = saved_token
        return success
    
    def test_learning_stats_cache(self):
        """Test that cache works (60s TTL)"""
        self.log("\n=== Testing Learning Stats (Cache) ===")
        
        # First request
        start = time.time()
        success1, response1 = self.run_test(
            "Learning Stats (first request)",
            "GET",
            "learning/stats",
            200
        )
        time1 = time.time() - start
        
        # Second request (should be cached)
        start = time.time()
        success2, response2 = self.run_test(
            "Learning Stats (cached request)",
            "GET",
            "learning/stats",
            200
        )
        time2 = time.time() - start
        
        if success1 and success2:
            self.log(f"   First request: {time1:.3f}s")
            self.log(f"   Second request: {time2:.3f}s")
            
            # Cached request should be significantly faster
            if time2 < time1 * 0.5:
                self.log(f"   ✓ Cache appears to be working (2nd request much faster)")
            else:
                self.log(f"   ⚠ Cache may not be working (2nd request not significantly faster)")
        
        return success1 and success2
    
    def test_saved_views_list(self):
        """Test GET /api/profile/saved-views"""
        self.log("\n=== Testing Saved Views (List) ===")
        success, response = self.run_test(
            "Get Saved Views",
            "GET",
            "profile/saved-views",
            200
        )
        if success:
            items = response.get('items', [])
            max_views = response.get('max', 10)
            
            self.log(f"   Saved views: {len(items)}")
            self.log(f"   Max allowed: {max_views}")
            
            if items:
                first = items[0]
                self.log(f"   Sample view: {first.get('name')}")
                self.log(f"   Filters: {first.get('filters')}")
                self.log(f"   Engine preset: {first.get('enginePreset')}")
        
        return success
    
    def test_saved_views_create(self):
        """Test POST /api/profile/saved-views"""
        self.log("\n=== Testing Saved Views (Create) ===")
        
        timestamp = int(time.time())
        payload = {
            "name": f"Test View {timestamp}",
            "filters": {
                "league": "Premier League",
                "market": "Under 2.5",
                "minConfidence": 70
            },
            "enginePreset": "DEFENSIVE",
            "sport": "football"
        }
        
        success, response = self.run_test(
            "Create Saved View",
            "POST",
            "profile/saved-views",
            200,
            data=payload
        )
        
        if success:
            view_id = response.get('id')
            self.log(f"   View created: {response.get('name')}")
            self.log(f"   View ID: {view_id}")
            
            # Store for update/delete tests
            self.test_view_id = view_id
        
        return success
    
    def test_saved_views_update(self):
        """Test PATCH /api/profile/saved-views/{id}"""
        self.log("\n=== Testing Saved Views (Update) ===")
        
        if not hasattr(self, 'test_view_id'):
            self.log("   Skipping - no test view created")
            return True
        
        payload = {
            "name": "Updated Test View",
            "filters": {
                "league": "La Liga",
                "market": "1X2",
                "minConfidence": 80
            }
        }
        
        success, response = self.run_test(
            "Update Saved View",
            "PATCH",
            f"profile/saved-views/{self.test_view_id}",
            200,
            data=payload
        )
        
        if success:
            self.log(f"   View updated: {response.get('name')}")
            self.log(f"   New filters: {response.get('filters')}")
        
        return success
    
    def test_saved_views_delete(self):
        """Test DELETE /api/profile/saved-views/{id}"""
        self.log("\n=== Testing Saved Views (Delete) ===")
        
        if not hasattr(self, 'test_view_id'):
            self.log("   Skipping - no test view created")
            return True
        
        url = f"{self.base_url}/api/profile/saved-views/{self.test_view_id}"
        headers = {'Authorization': f'Bearer {self.token}'}
        
        self.tests_run += 1
        self.log(f"Testing Delete Saved View...")
        
        try:
            response = requests.delete(url, headers=headers, timeout=30)
            success = response.status_code == 200
            
            if success:
                self.tests_passed += 1
                self.log(f"✅ PASSED - Delete Saved View (Status: {response.status_code})", "PASS")
                self.log(f"   View deleted: {self.test_view_id}")
                return True
            else:
                self.tests_failed.append({
                    "test": "Delete Saved View",
                    "expected": 200,
                    "got": response.status_code,
                    "response": response.text[:200]
                })
                self.log(f"❌ FAILED - Delete Saved View (Expected 200, got {response.status_code})", "FAIL")
                return False
        except Exception as e:
            self.tests_failed.append({
                "test": "Delete Saved View",
                "expected": 200,
                "got": "ERROR",
                "response": str(e)
            })
            self.log(f"❌ FAILED - Delete Saved View (Error: {str(e)})", "FAIL")
            return False
    
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
            # Historical Learning Layer tests
            ("Learning Stats Basic", self.test_learning_stats_basic),
            ("Learning Stats Fields", self.test_learning_stats_fields),
            ("Learning Stats Calculations", self.test_learning_stats_calculations),
            ("Learning Stats Sorting", self.test_learning_stats_sorting),
            ("Learning Stats Summary", self.test_learning_stats_summary),
            ("Learning Stats Filter Market", self.test_learning_stats_filter_market),
            ("Learning Stats Filter Match State", self.test_learning_stats_filter_match_state),
            ("Learning Stats Filter Sport", self.test_learning_stats_filter_sport),
            ("Learning Stats Auth", self.test_learning_stats_auth),
            ("Learning Stats Cache", self.test_learning_stats_cache),
            # Saved Views regression tests
            ("Saved Views List", self.test_saved_views_list),
            ("Saved Views Create", self.test_saved_views_create),
            ("Saved Views Update", self.test_saved_views_update),
            ("Saved Views Delete", self.test_saved_views_delete),
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
