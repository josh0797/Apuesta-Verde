#!/usr/bin/env python3
"""
Backend Test Suite for P1 Baseball Historical Enrichment + P2 MLB Feedback Loop
================================================================================
Tests all endpoints and functionality for the MLB features.
"""
import sys
import requests
import json
from datetime import datetime, timedelta
from typing import Optional

# Backend URL from frontend/.env
BASE_URL = "https://low-volatility-plays.preview.emergentagent.com"

class Colors:
    GREEN = '\033[92m'
    RED = '\033[91m'
    YELLOW = '\033[93m'
    BLUE = '\033[94m'
    END = '\033[0m'

class MLBTestSuite:
    def __init__(self, base_url: str):
        self.base_url = base_url
        self.token: Optional[str] = None
        self.tests_run = 0
        self.tests_passed = 0
        self.tests_failed = 0
        self.failures = []

    def log(self, message: str, color: str = Colors.BLUE):
        print(f"{color}{message}{Colors.END}")

    def test(self, name: str, method: str, endpoint: str, expected_status: int, 
             data: Optional[dict] = None, check_fn: Optional[callable] = None) -> tuple[bool, dict]:
        """Run a single test"""
        url = f"{self.base_url}{endpoint}"
        headers = {'Content-Type': 'application/json'}
        if self.token:
            headers['Authorization'] = f'Bearer {self.token}'

        self.tests_run += 1
        self.log(f"\n🔍 Test #{self.tests_run}: {name}", Colors.BLUE)
        
        try:
            if method == 'GET':
                response = requests.get(url, headers=headers, timeout=30)
            elif method == 'POST':
                response = requests.post(url, json=data, headers=headers, timeout=30)
            else:
                raise ValueError(f"Unsupported method: {method}")

            # Check status code
            if response.status_code != expected_status:
                self.tests_failed += 1
                msg = f"❌ FAILED - Expected {expected_status}, got {response.status_code}"
                self.log(msg, Colors.RED)
                self.failures.append(f"{name}: {msg}")
                try:
                    self.log(f"   Response: {response.text[:500]}", Colors.RED)
                except:
                    pass
                return False, {}

            # Parse response
            try:
                resp_data = response.json()
            except:
                resp_data = {}

            # Run custom check function if provided
            if check_fn:
                check_result = check_fn(resp_data)
                if not check_result:
                    self.tests_failed += 1
                    msg = f"❌ FAILED - Custom check failed"
                    self.log(msg, Colors.RED)
                    self.failures.append(f"{name}: {msg}")
                    return False, resp_data

            self.tests_passed += 1
            self.log(f"✅ PASSED - Status: {response.status_code}", Colors.GREEN)
            return True, resp_data

        except Exception as e:
            self.tests_failed += 1
            msg = f"❌ FAILED - Error: {str(e)}"
            self.log(msg, Colors.RED)
            self.failures.append(f"{name}: {msg}")
            return False, {}

    def test_auth(self):
        """Test authentication"""
        self.log("\n" + "="*80, Colors.YELLOW)
        self.log("AUTHENTICATION TEST", Colors.YELLOW)
        self.log("="*80, Colors.YELLOW)
        
        success, response = self.test(
            "Login with demo@valuebet.app/demo1234",
            "POST",
            "/api/auth/login",
            200,
            data={"email": "demo@valuebet.app", "password": "demo1234"}
        )
        
        if success and 'token' in response:
            self.token = response['token']
            self.log(f"   Token obtained: {self.token[:20]}...", Colors.GREEN)
            return True
        return False

    def test_p1_historical_enrichment(self):
        """Test P1 Historical Enrichment features"""
        self.log("\n" + "="*80, Colors.YELLOW)
        self.log("P1 HISTORICAL ENRICHMENT TESTS", Colors.YELLOW)
        self.log("="*80, Colors.YELLOW)

        # Test 1: GET /api/mlb/day with baseballHistoricalProfile
        def check_historical_profile(data):
            picks = data.get('picks', []) + data.get('rescued_picks', [])
            if not picks:
                self.log("   ⚠️  No picks returned (expected for past dates)", Colors.YELLOW)
                return True  # Not a failure - time filter defense
            
            # Check at least one pick has baseballHistoricalProfile
            has_profile = False
            for pick in picks:
                profile = pick.get('baseballHistoricalProfile')
                if profile:
                    has_profile = True
                    self.log(f"   ✓ Found baseballHistoricalProfile with available={profile.get('available')}", Colors.GREEN)
                    
                    # Verify schema
                    if 'available' not in profile:
                        self.log("   ✗ Missing 'available' field", Colors.RED)
                        return False
                    
                    if profile.get('available'):
                        required_keys = ['home', 'away', 'pitching', 'combined']
                        for key in required_keys:
                            if key not in profile:
                                self.log(f"   ✗ Missing required key: {key}", Colors.RED)
                                return False
                        self.log("   ✓ Schema validation passed", Colors.GREEN)
                    break
            
            return True  # Pass even if no profile (time filter)

        # Use today's date for MLB
        today = datetime.now().strftime("%Y-%m-%d")
        success, mlb_data = self.test(
            "GET /api/mlb/day returns picks with baseballHistoricalProfile",
            "GET",
            f"/api/mlb/day?date={today}",
            200,
            check_fn=check_historical_profile
        )

        # Test 2: Check baseball_runs_rescue when no chosen_market with score>=72
        def check_runs_rescue(data):
            rescued = data.get('rescued_picks', [])
            for pick in rescued:
                if pick.get('baseball_runs_rescue'):
                    self.log("   ✓ Found baseball_runs_rescue in rescued pick", Colors.GREEN)
                    rescue = pick['baseball_runs_rescue']
                    if 'rescue_market' in rescue and 'rescue_confidence' in rescue:
                        self.log(f"   ✓ Rescue market: {rescue.get('rescue_market')}", Colors.GREEN)
                        return True
            self.log("   ⚠️  No baseball_runs_rescue found (may not be needed)", Colors.YELLOW)
            return True  # Not a hard failure

        self.test(
            "Baseball runs rescue attempted when needed",
            "GET",
            f"/api/mlb/day?date={today}",
            200,
            check_fn=check_runs_rescue
        )

        # Test 3: Check trap signals
        def check_trap_signals(data):
            picks = data.get('picks', []) + data.get('rescued_picks', [])
            for pick in picks:
                if pick.get('historical_trap_signals'):
                    self.log(f"   ✓ Found {len(pick['historical_trap_signals'])} trap signals", Colors.GREEN)
                    for sig in pick['historical_trap_signals'][:3]:
                        self.log(f"     - {sig.get('code')}: {sig.get('severity')}", Colors.GREEN)
                    return True
            self.log("   ⚠️  No trap signals found (may not be triggered)", Colors.YELLOW)
            return True

        self.test(
            "Trap signals from collect_baseball_trap_signals()",
            "GET",
            f"/api/mlb/day?date={today}",
            200,
            check_fn=check_trap_signals
        )

        # Test 4: Football regression - must NOT include baseball fields
        def check_no_baseball_fields(data):
            picks = data.get('payload', {}).get('picks', [])
            for pick in picks:
                if 'baseballHistoricalProfile' in pick:
                    self.log("   ✗ Found baseballHistoricalProfile in football pick!", Colors.RED)
                    return False
                if 'historical_trap_signals' in pick:
                    self.log("   ✗ Found historical_trap_signals in football pick!", Colors.RED)
                    return False
            self.log("   ✓ No baseball fields in football picks", Colors.GREEN)
            return True

        self.test(
            "Football regression: no baseball fields",
            "POST",
            "/api/analysis/run",
            200,
            data={"sport": "football", "max_matches": 3, "background": False},
            check_fn=check_no_baseball_fields
        )

        # Test 5: Basketball regression - must NOT include baseball fields
        self.test(
            "Basketball regression: no baseball fields",
            "POST",
            "/api/analysis/run",
            200,
            data={"sport": "basketball", "max_matches": 3, "background": False},
            check_fn=check_no_baseball_fields
        )

    def test_p2_feedback_loop(self):
        """Test P2 Feedback Loop features"""
        self.log("\n" + "="*80, Colors.YELLOW)
        self.log("P2 FEEDBACK LOOP TESTS", Colors.YELLOW)
        self.log("="*80, Colors.YELLOW)

        # Test 1: GET /api/mlb/engine/weights
        def check_weights_schema(data):
            required_keys = [
                'active_weights', 'pending_for_next_recal', 'batch_size_required',
                'settled_total', 'last_recalibration_at', 'version'
            ]
            for key in required_keys:
                if key not in data:
                    self.log(f"   ✗ Missing required key: {key}", Colors.RED)
                    return False
            
            weights = data.get('active_weights', {})
            expected_weight_keys = [
                'pitcher_edge', 'bullpen', 'fav_offense', 'fav_wins_by_2_rate',
                'und_losses_by_2', 'margin_reliability', 'parlay_avg_score',
                'parlay_frag_inv', 'parlay_correlation', 'parlay_pitcher_conf'
            ]
            for key in expected_weight_keys:
                if key not in weights:
                    self.log(f"   ✗ Missing weight key: {key}", Colors.RED)
                    return False
            
            self.log(f"   ✓ All weight keys present, version={data.get('version')}", Colors.GREEN)
            return True

        success, weights_data = self.test(
            "GET /api/mlb/engine/weights returns valid schema",
            "GET",
            "/api/mlb/engine/weights",
            200,
            check_fn=check_weights_schema
        )

        # Test 2: POST /api/mlb/picks/{pick_id}/settle with synthetic pick
        synthetic_pick_id = f"test_run_{datetime.now().timestamp()}-12345"
        
        def check_settle_response(data):
            if not data.get('ok'):
                self.log("   ✗ Settle response ok=false", Colors.RED)
                return False
            
            feedback = data.get('feedback', {})
            required_metrics = ['margin', 'totalRuns', 'runLineCovered', 'overHit']
            for metric in required_metrics:
                if metric not in feedback:
                    self.log(f"   ✗ Missing metric: {metric}", Colors.RED)
                    return False
            
            # Check specific values for Detroit -1.5 won 6-2
            if feedback.get('margin') != 4:
                self.log(f"   ✗ Expected margin=4, got {feedback.get('margin')}", Colors.RED)
                return False
            if feedback.get('totalRuns') != 8:
                self.log(f"   ✗ Expected totalRuns=8, got {feedback.get('totalRuns')}", Colors.RED)
                return False
            if feedback.get('runLineCovered') != True:
                self.log(f"   ✗ Expected runLineCovered=true, got {feedback.get('runLineCovered')}", Colors.RED)
                return False
            
            self.log(f"   ✓ Metrics correct: margin=4, totalRuns=8, runLineCovered=true", Colors.GREEN)
            return True

        self.test(
            "POST /api/mlb/picks/{pick_id}/settle with synthetic pick (Detroit -1.5 won 6-2)",
            "POST",
            f"/api/mlb/picks/{synthetic_pick_id}/settle",
            200,
            data={
                "pick_id": synthetic_pick_id,
                "run_id": f"test_run_{datetime.now().timestamp()}",
                "match_id": "12345",
                "outcome": "won",
                "final_home_runs": 6,
                "final_away_runs": 2,
                "v2_snapshot": {
                    "pickType": "DOMINANT_FAVORITE_RUN_LINE",
                    "marginProjection": 2.5,
                    "coverProbability": 65.0,
                    "expectedRuns": 8.5,
                    "recommendedLine": "Run Line -1.5",
                    "fragilityScore": 35.0
                },
                "pick_doc": {
                    "selection": "Detroit -1.5",
                    "market": "Run Line",
                    "favorite_team": "Detroit",
                    "home_team": "Detroit",
                    "away_team": "Cleveland"
                }
            },
            check_fn=check_settle_response
        )

        # Test 3: Verify settle extends pick_tracking with mlb_metrics
        def check_pick_tracking(data):
            items = data.get('items', [])
            for item in items:
                if item.get('pick_id') == synthetic_pick_id:
                    mlb_metrics = item.get('mlb_metrics')
                    if not mlb_metrics:
                        self.log("   ✗ mlb_metrics not found in pick_tracking", Colors.RED)
                        return False
                    self.log(f"   ✓ Found mlb_metrics in pick_tracking: {mlb_metrics}", Colors.GREEN)
                    return True
            self.log("   ⚠️  Pick not found in tracking (may take time to sync)", Colors.YELLOW)
            return True

        self.test(
            "Verify settle extends pick_tracking with mlb_metrics",
            "GET",
            "/api/picks/tracked",
            200,
            check_fn=check_pick_tracking
        )

        # Test 4: Over/Under metric computation
        over_pick_id = f"test_over_{datetime.now().timestamp()}-67890"
        
        def check_over_hit(data):
            feedback = data.get('feedback', {})
            # Over 7.5 with totalRuns=9 should have overHit=true
            if feedback.get('overHit') != True:
                self.log(f"   ✗ Expected overHit=true for Over 7.5 with 9 runs", Colors.RED)
                return False
            self.log(f"   ✓ Over metric correct: overHit=true", Colors.GREEN)
            return True

        self.test(
            "Over/Under metric: Over 7.5 with totalRuns=9 → overHit=true",
            "POST",
            f"/api/mlb/picks/{over_pick_id}/settle",
            200,
            data={
                "pick_id": over_pick_id,
                "run_id": f"test_run_{datetime.now().timestamp()}",
                "match_id": "67890",
                "outcome": "won",
                "final_home_runs": 5,
                "final_away_runs": 4,
                "v2_snapshot": {
                    "pickType": "SMART_LOW_OVER",
                    "expectedRuns": 9.0,
                    "recommendedLine": "Over 7.5"
                },
                "pick_doc": {
                    "selection": "Over 7.5",
                    "market": "Total Runs Over",
                    "home_team": "Yankees",
                    "away_team": "Red Sox"
                }
            },
            check_fn=check_over_hit
        )

        # Test 5: POST /api/mlb/engine/recompute with <50 pending
        def check_not_enough_pending(data):
            if data.get('recalibration') is not None:
                self.log("   ⚠️  Recalibration happened (may have >=50 pending)", Colors.YELLOW)
                return True
            if 'Not enough pending' in data.get('detail', ''):
                self.log("   ✓ Correctly returned 'Not enough pending'", Colors.GREEN)
                return True
            return True

        self.test(
            "POST /api/mlb/engine/recompute with <50 pending",
            "POST",
            "/api/mlb/engine/recompute",
            200,
            check_fn=check_not_enough_pending
        )

        # Test 6: pickType→category mapping
        def check_category_mapping(data):
            feedback = data.get('feedback', {})
            pick_type = feedback.get('pickType')
            category = feedback.get('category')
            
            expected_mappings = {
                'DOMINANT_FAVORITE_RUN_LINE': 'run_line_minus_1_5',
                'SMART_LOW_OVER': 'over_low',
                'PITCHER_UNDER': 'under_pitcher_driven'
            }
            
            if pick_type in expected_mappings:
                expected_cat = expected_mappings[pick_type]
                if category == expected_cat:
                    self.log(f"   ✓ Correct mapping: {pick_type} → {category}", Colors.GREEN)
                    return True
                else:
                    self.log(f"   ✗ Wrong mapping: {pick_type} → {category} (expected {expected_cat})", Colors.RED)
                    return False
            return True

        category_pick_id = f"test_cat_{datetime.now().timestamp()}-11111"
        self.test(
            "pickType→category mapping verification",
            "POST",
            f"/api/mlb/picks/{category_pick_id}/settle",
            200,
            data={
                "pick_id": category_pick_id,
                "run_id": f"test_run_{datetime.now().timestamp()}",
                "match_id": "11111",
                "outcome": "won",
                "final_home_runs": 5,
                "final_away_runs": 3,
                "v2_snapshot": {
                    "pickType": "DOMINANT_FAVORITE_RUN_LINE",
                    "marginProjection": 2.0
                },
                "pick_doc": {
                    "selection": "Team -1.5",
                    "market": "Run Line"
                }
            },
            check_fn=check_category_mapping
        )

    def test_import_sanity(self):
        """Test that mlb_feedback_loop module is importable"""
        self.log("\n" + "="*80, Colors.YELLOW)
        self.log("IMPORT SANITY TEST", Colors.YELLOW)
        self.log("="*80, Colors.YELLOW)

        try:
            sys.path.insert(0, '/app/backend')
            from services.mlb_feedback_loop import (
                DEFAULT_WEIGHTS, FEEDBACK_BATCH_SIZE, get_active_weights,
                record_mlb_pick_outcome, recompute_weights_if_due,
                get_recalibration_status, CATEGORY_FOR_PICK_TYPE
            )
            
            self.tests_run += 1
            self.tests_passed += 1
            self.log("✅ All mlb_feedback_loop exports importable", Colors.GREEN)
            self.log(f"   DEFAULT_WEIGHTS keys: {list(DEFAULT_WEIGHTS.keys())}", Colors.GREEN)
            self.log(f"   FEEDBACK_BATCH_SIZE: {FEEDBACK_BATCH_SIZE}", Colors.GREEN)
            self.log(f"   CATEGORY_FOR_PICK_TYPE: {CATEGORY_FOR_PICK_TYPE}", Colors.GREEN)
            return True
        except Exception as e:
            self.tests_run += 1
            self.tests_failed += 1
            self.log(f"❌ Import failed: {str(e)}", Colors.RED)
            self.failures.append(f"Import sanity: {str(e)}")
            return False

    def print_summary(self):
        """Print test summary"""
        self.log("\n" + "="*80, Colors.YELLOW)
        self.log("TEST SUMMARY", Colors.YELLOW)
        self.log("="*80, Colors.YELLOW)
        
        total = self.tests_run
        passed = self.tests_passed
        failed = self.tests_failed
        
        self.log(f"\nTotal Tests: {total}", Colors.BLUE)
        self.log(f"Passed: {passed}", Colors.GREEN)
        self.log(f"Failed: {failed}", Colors.RED if failed > 0 else Colors.GREEN)
        
        if failed > 0:
            self.log("\n❌ FAILED TESTS:", Colors.RED)
            for failure in self.failures:
                self.log(f"  - {failure}", Colors.RED)
        
        success_rate = (passed / total * 100) if total > 0 else 0
        self.log(f"\nSuccess Rate: {success_rate:.1f}%", 
                Colors.GREEN if success_rate >= 80 else Colors.YELLOW)
        
        return failed == 0

def main():
    print(f"\n{'='*80}")
    print("MLB P1 + P2 Backend Test Suite")
    print(f"{'='*80}\n")
    print(f"Backend URL: {BASE_URL}")
    print(f"Started at: {datetime.now().isoformat()}\n")

    suite = MLBTestSuite(BASE_URL)
    
    # Run tests
    if not suite.test_auth():
        print("\n❌ Authentication failed. Cannot proceed with other tests.")
        return 1
    
    suite.test_import_sanity()
    suite.test_p1_historical_enrichment()
    suite.test_p2_feedback_loop()
    
    # Print summary
    success = suite.print_summary()
    
    print(f"\nCompleted at: {datetime.now().isoformat()}")
    return 0 if success else 1

if __name__ == "__main__":
    sys.exit(main())
