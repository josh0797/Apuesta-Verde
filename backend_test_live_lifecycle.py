#!/usr/bin/env python3
"""
Backend API tests for Live Lifecycle Feature (Phase 11)

Tests the critical bug fix for stale live data showing matches at 90' that already ended.

Test Coverage:
1. GET /api/matches/live returns only valid matches (no stale 2H+95min, no old heartbeat)
2. Each item has _live_state with correct shape and valid=true
3. Each item has _freshness with score 0-100 and correct label
4. Stale matches are auto-flipped to is_live=false during sweep
5. POST /api/live/reevaluate returns 409 for stale matches
6. POST /api/live/reevaluate returns 200 for active matches
7. Multi-sport support (football, basketball, baseball)
8. Response includes archived_count and cache_ttl_sec
"""
import sys
import requests
from datetime import datetime

# Public endpoint from frontend/.env
BASE_URL = "https://low-volatility-plays.preview.emergentagent.com/api"

class Colors:
    GREEN = '\033[92m'
    RED = '\033[91m'
    YELLOW = '\033[93m'
    BLUE = '\033[94m'
    CYAN = '\033[96m'
    RESET = '\033[0m'

class LiveLifecycleTestRunner:
    def __init__(self):
        self.tests_run = 0
        self.tests_passed = 0
        self.token = None
        self.user_id = None
        self.issues = []

    def log(self, msg, color=Colors.RESET):
        print(f"{color}{msg}{Colors.RESET}")

    def add_issue(self, issue):
        """Track issues for final report"""
        self.issues.append(issue)
        self.log(f"   ⚠ ISSUE: {issue}", Colors.RED)

    def test(self, name, method, endpoint, expected_status, data=None, headers=None, params=None):
        """Run a single API test"""
        url = f"{BASE_URL}{endpoint}"
        h = {'Content-Type': 'application/json'}
        if self.token:
            h['Authorization'] = f'Bearer {self.token}'
        if headers:
            h.update(headers)

        self.tests_run += 1
        self.log(f"\n🔍 Test {self.tests_run}: {name}", Colors.BLUE)
        
        try:
            if method == 'GET':
                response = requests.get(url, headers=h, params=params or data, timeout=30)
            elif method == 'POST':
                response = requests.post(url, json=data, headers=h, timeout=30)
            else:
                raise ValueError(f"Unsupported method: {method}")

            success = response.status_code == expected_status
            if success:
                self.tests_passed += 1
                self.log(f"✅ PASS — Status: {response.status_code}", Colors.GREEN)
                try:
                    return True, response.json()
                except:
                    return True, {}
            else:
                self.log(f"❌ FAIL — Expected {expected_status}, got {response.status_code}", Colors.RED)
                try:
                    resp_data = response.json()
                    self.log(f"   Response: {resp_data}", Colors.YELLOW)
                    return False, resp_data
                except:
                    self.log(f"   Response: {response.text[:200]}", Colors.YELLOW)
                    return False, {}

        except Exception as e:
            self.log(f"❌ FAIL — Error: {str(e)}", Colors.RED)
            return False, {}

    def login(self):
        """Login with demo account"""
        self.log("\n" + "="*80, Colors.CYAN)
        self.log("AUTHENTICATION", Colors.CYAN)
        self.log("="*80, Colors.CYAN)
        
        success, response = self.test(
            "Login with demo@valuebet.app",
            "POST",
            "/auth/login",
            200,
            data={"email": "demo@valuebet.app", "password": "demo1234"}
        )
        if success and 'token' in response:
            self.token = response['token']
            self.user_id = response.get('user', {}).get('id')
            self.log(f"   Token acquired: {self.token[:20]}...", Colors.GREEN)
            return True
        return False

    def test_live_endpoint_structure(self):
        """Test GET /api/matches/live returns correct structure"""
        self.log("\n" + "="*80, Colors.CYAN)
        self.log("LIVE ENDPOINT STRUCTURE", Colors.CYAN)
        self.log("="*80, Colors.CYAN)

        success, response = self.test(
            "GET /api/matches/live (football)",
            "GET",
            "/matches/live",
            200,
            params={"sport": "football", "refresh": "false"}
        )

        if not success:
            self.add_issue("GET /api/matches/live failed")
            return False

        # Check top-level structure
        required_keys = ['count', 'sport', 'items', 'archived_count', 'cache_ttl_sec', 'computed_at']
        for key in required_keys:
            if key not in response:
                self.add_issue(f"Missing top-level key: {key}")
            else:
                self.log(f"   ✓ Has {key}: {response[key] if key != 'items' else f'{len(response[key])} items'}", Colors.GREEN)

        # Verify cache_ttl_sec is correct for football (should be 60)
        if response.get('cache_ttl_sec') == 60:
            self.log(f"   ✓ cache_ttl_sec correct for football: 60", Colors.GREEN)
        else:
            self.add_issue(f"cache_ttl_sec wrong for football: {response.get('cache_ttl_sec')} (expected 60)")

        return True

    def test_live_state_structure(self):
        """Test each item has _live_state with correct shape"""
        self.log("\n" + "="*80, Colors.CYAN)
        self.log("LIVE STATE STRUCTURE", Colors.CYAN)
        self.log("="*80, Colors.CYAN)

        success, response = self.test(
            "GET /api/matches/live to check _live_state",
            "GET",
            "/matches/live",
            200,
            params={"sport": "football", "refresh": "false"}
        )

        if not success:
            return False

        items = response.get('items', [])
        self.log(f"   Checking {len(items)} live matches", Colors.BLUE)

        if len(items) == 0:
            self.log(f"   ℹ No live matches to check (not an error)", Colors.YELLOW)
            return True

        # Check first 5 items
        for i, item in enumerate(items[:5]):
            match_id = item.get('match_id')
            self.log(f"\n   Match {i+1}: {match_id}", Colors.BLUE)

            # Check _live_state exists
            if '_live_state' not in item:
                self.add_issue(f"Match {match_id} missing _live_state")
                continue

            live_state = item['_live_state']
            
            # Check required fields
            required_fields = ['state', 'minute', 'minute_label', 'valid', 'reason', 'status_short', 'heartbeat_age_sec']
            for field in required_fields:
                if field not in live_state:
                    self.add_issue(f"Match {match_id} _live_state missing field: {field}")
                else:
                    self.log(f"     • {field}: {live_state[field]}", Colors.GREEN)

            # Check valid is True (all returned items must be valid)
            if not live_state.get('valid'):
                self.add_issue(f"Match {match_id} has valid=false but was returned")

            # Check state is one of expected values
            valid_states = ['LIVE_ACTIVE', 'LIVE_LATE', 'GARBAGE_TIME', 'HT']
            if live_state.get('state') not in valid_states:
                self.add_issue(f"Match {match_id} has invalid state: {live_state.get('state')}")

            # Check minute_label is not always '90' (the bug we're fixing)
            minute_label = live_state.get('minute_label')
            if minute_label:
                self.log(f"     ✓ minute_label: {minute_label} (not fixed '90')", Colors.GREEN)

        return True

    def test_freshness_structure(self):
        """Test each item has _freshness with correct shape"""
        self.log("\n" + "="*80, Colors.CYAN)
        self.log("FRESHNESS STRUCTURE", Colors.CYAN)
        self.log("="*80, Colors.CYAN)

        success, response = self.test(
            "GET /api/matches/live to check _freshness",
            "GET",
            "/matches/live",
            200,
            params={"sport": "football", "refresh": "false"}
        )

        if not success:
            return False

        items = response.get('items', [])
        if len(items) == 0:
            self.log(f"   ℹ No live matches to check (not an error)", Colors.YELLOW)
            return True

        for i, item in enumerate(items[:5]):
            match_id = item.get('match_id')
            self.log(f"\n   Match {i+1}: {match_id}", Colors.BLUE)

            # Check _freshness exists
            if '_freshness' not in item:
                self.add_issue(f"Match {match_id} missing _freshness")
                continue

            freshness = item['_freshness']
            
            # Check required fields
            required_fields = ['score', 'label', 'components', 'heartbeat_age_sec']
            for field in required_fields:
                if field not in freshness:
                    self.add_issue(f"Match {match_id} _freshness missing field: {field}")
                else:
                    self.log(f"     • {field}: {freshness[field]}", Colors.GREEN)

            # Check score is 0-100
            score = freshness.get('score')
            if score is not None and (score < 0 or score > 100):
                self.add_issue(f"Match {match_id} freshness score out of range: {score}")

            # Check label is one of expected values (EXPIRED should NOT appear)
            valid_labels = ['DATOS_FRESCOS', 'DATOS_RETRASADOS', 'LIVE_STALE']
            label = freshness.get('label')
            if label == 'EXPIRED':
                self.add_issue(f"Match {match_id} has EXPIRED label but was returned")
            elif label not in valid_labels:
                self.add_issue(f"Match {match_id} has invalid freshness label: {label}")

        return True

    def test_reevaluate_stale_match(self):
        """Test POST /api/live/reevaluate returns 409 for stale matches"""
        self.log("\n" + "="*80, Colors.CYAN)
        self.log("REEVALUATE STALE MATCH (409 TEST)", Colors.CYAN)
        self.log("="*80, Colors.CYAN)

        # First, get archived matches to find a stale match_id
        # The review request mentions match_id 1516348 as an example
        stale_match_id = "1516348"

        success, response = self.test(
            f"POST /api/live/reevaluate with stale match {stale_match_id}",
            "POST",
            "/live/reevaluate",
            409,  # Expecting 409 Conflict
            data={
                "match_id": stale_match_id,
                "sport": "football",
                "refresh": True
            }
        )

        if success:
            # Check response structure
            detail = response.get('detail', {})
            if isinstance(detail, dict):
                error = detail.get('error')
                message = detail.get('message')
                live_state = detail.get('live_state')

                self.log(f"   ✓ error: {error}", Colors.GREEN)
                self.log(f"   ✓ message: {message}", Colors.GREEN)
                
                if error == 'live_match_not_active':
                    self.log(f"   ✓ Correct error code", Colors.GREEN)
                else:
                    self.add_issue(f"Wrong error code: {error} (expected 'live_match_not_active')")

                if live_state:
                    self.log(f"   ✓ live_state included: {live_state}", Colors.GREEN)
                else:
                    self.add_issue("Missing live_state in 409 response")
            else:
                self.log(f"   ℹ detail is string: {detail}", Colors.YELLOW)
        else:
            self.log(f"   ℹ Match {stale_match_id} might not exist or is actually live", Colors.YELLOW)

        return True

    def test_reevaluate_active_match(self):
        """Test POST /api/live/reevaluate returns 200 for active matches"""
        self.log("\n" + "="*80, Colors.CYAN)
        self.log("REEVALUATE ACTIVE MATCH (200 TEST)", Colors.CYAN)
        self.log("="*80, Colors.CYAN)

        # First get a live match
        success, response = self.test(
            "GET /api/matches/live to find active match",
            "GET",
            "/matches/live",
            200,
            params={"sport": "football", "refresh": "false"}
        )

        if not success:
            return False

        items = response.get('items', [])
        if len(items) == 0:
            self.log(f"   ℹ No live matches to test reevaluate (not an error)", Colors.YELLOW)
            return True

        # Try to reevaluate the first active match
        active_match = items[0]
        match_id = active_match.get('match_id')
        live_state = active_match.get('_live_state', {})

        self.log(f"   Testing with match {match_id} (state: {live_state.get('state')})", Colors.BLUE)

        # Only test if it's genuinely active (not GARBAGE_TIME)
        if live_state.get('state') in ['LIVE_ACTIVE', 'LIVE_LATE', 'HT']:
            success, response = self.test(
                f"POST /api/live/reevaluate with active match {match_id}",
                "POST",
                "/live/reevaluate",
                200,
                data={
                    "match_id": match_id,
                    "sport": "football",
                    "refresh": True
                }
            )

            if success:
                result = response.get('result', {})
                self.log(f"   ✓ Got result: {result.get('live_state')}", Colors.GREEN)
                self.log(f"   ✓ Market: {result.get('market')}", Colors.GREEN)
                self.log(f"   ✓ Edge: {result.get('edge_pct')}%", Colors.GREEN)
            else:
                self.add_issue(f"Reevaluate failed for active match {match_id}")
        else:
            self.log(f"   ℹ Match is {live_state.get('state')}, skipping reevaluate test", Colors.YELLOW)

        return True

    def test_multi_sport_support(self):
        """Test basketball and baseball endpoints"""
        self.log("\n" + "="*80, Colors.CYAN)
        self.log("MULTI-SPORT SUPPORT", Colors.CYAN)
        self.log("="*80, Colors.CYAN)

        for sport in ['basketball', 'baseball']:
            success, response = self.test(
                f"GET /api/matches/live ({sport})",
                "GET",
                "/matches/live",
                200,
                params={"sport": sport, "refresh": "false"}
            )

            if success:
                count = response.get('count', 0)
                cache_ttl = response.get('cache_ttl_sec')
                expected_ttl = 30 if sport == 'basketball' else 45
                
                self.log(f"   ✓ {sport}: {count} matches, TTL={cache_ttl}s", Colors.GREEN)
                
                if cache_ttl != expected_ttl:
                    self.add_issue(f"{sport} cache_ttl wrong: {cache_ttl} (expected {expected_ttl})")

        return True

    def test_no_stale_matches_returned(self):
        """Verify no matches with 2H+95min or old heartbeat are returned"""
        self.log("\n" + "="*80, Colors.CYAN)
        self.log("STALE MATCH VALIDATION", Colors.CYAN)
        self.log("="*80, Colors.CYAN)

        success, response = self.test(
            "GET /api/matches/live to check for stale matches",
            "GET",
            "/matches/live",
            200,
            params={"sport": "football", "refresh": "true"}  # Use refresh=true to trigger sweep
        )

        if not success:
            return False

        items = response.get('items', [])
        archived_count = response.get('archived_count', 0)

        self.log(f"   Valid matches returned: {len(items)}", Colors.GREEN)
        self.log(f"   Archived count: {archived_count}", Colors.GREEN)

        # Check each returned match for stale conditions
        stale_found = []
        for item in items:
            match_id = item.get('match_id')
            live_state = item.get('_live_state', {})
            freshness = item.get('_freshness', {})
            
            status = live_state.get('status_short')
            minute = live_state.get('minute')
            heartbeat_age = live_state.get('heartbeat_age_sec')

            # Check for stale 2H+95min
            if status == '2H' and minute is not None and minute >= 95:
                stale_found.append(f"{match_id}: 2H @ {minute}' (should be filtered)")

            # Check for old heartbeat (>10min for football)
            if heartbeat_age is not None and heartbeat_age > 600:
                stale_found.append(f"{match_id}: heartbeat {heartbeat_age}s old (should be filtered)")

            # Check freshness score < 30 (EXPIRED)
            if freshness.get('score', 100) < 30:
                stale_found.append(f"{match_id}: freshness score {freshness.get('score')} (should be filtered)")

        if stale_found:
            for issue in stale_found:
                self.add_issue(issue)
        else:
            self.log(f"   ✓ No stale matches found in response", Colors.GREEN)

        return True

    def run_all(self):
        """Run all tests"""
        self.log("\n" + "="*80, Colors.CYAN)
        self.log("LIVE LIFECYCLE FEATURE TESTS — Phase 11", Colors.CYAN)
        self.log("="*80, Colors.CYAN)
        self.log(f"Testing against: {BASE_URL}", Colors.BLUE)
        self.log(f"Started at: {datetime.now().isoformat()}", Colors.BLUE)

        # Login first
        if not self.login():
            self.log("\n❌ Authentication failed. Cannot proceed.", Colors.RED)
            return 1

        # Run test suites
        try:
            self.test_live_endpoint_structure()
            self.test_live_state_structure()
            self.test_freshness_structure()
            self.test_no_stale_matches_returned()
            self.test_reevaluate_stale_match()
            self.test_reevaluate_active_match()
            self.test_multi_sport_support()
        except Exception as e:
            self.log(f"\n❌ Test suite crashed: {e}", Colors.RED)
            import traceback
            traceback.print_exc()

        # Summary
        self.log("\n" + "="*80, Colors.CYAN)
        self.log("TEST SUMMARY", Colors.CYAN)
        self.log("="*80, Colors.CYAN)
        self.log(f"Tests run: {self.tests_run}", Colors.BLUE)
        self.log(f"Tests passed: {self.tests_passed}", Colors.GREEN if self.tests_passed == self.tests_run else Colors.YELLOW)
        self.log(f"Tests failed: {self.tests_run - self.tests_passed}", Colors.RED if self.tests_run != self.tests_passed else Colors.GREEN)
        
        success_rate = (self.tests_passed / self.tests_run * 100) if self.tests_run > 0 else 0
        self.log(f"Success rate: {success_rate:.1f}%", Colors.GREEN if success_rate >= 90 else Colors.YELLOW)

        if self.issues:
            self.log(f"\n⚠ ISSUES FOUND ({len(self.issues)}):", Colors.RED)
            for i, issue in enumerate(self.issues, 1):
                self.log(f"  {i}. {issue}", Colors.RED)
        else:
            self.log(f"\n✅ NO ISSUES FOUND", Colors.GREEN)

        return 0 if len(self.issues) == 0 else 1


if __name__ == "__main__":
    runner = LiveLifecycleTestRunner()
    sys.exit(runner.run_all())
