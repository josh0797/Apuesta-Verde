"""Backend API Testing for Fix 1 (MLB feedback loop + Basketball warehouse)
and Fix 2 (Statcast warehouse-first lookup).

Tests:
1. Login with demo credentials
2. API endpoints alive (picks, analysis, meta)
3. Clean imports for new modules
4. Sport isolation (basketball reason codes and collections)
5. Fail-soft behavior
6. Sample-size gates
7. Basketball pattern detection
"""
import sys
import requests
from datetime import datetime

class BackendTester:
    def __init__(self, base_url="https://low-volatility-plays.preview.emergentagent.com"):
        self.base_url = base_url
        self.token = None
        self.tests_run = 0
        self.tests_passed = 0
        self.failed_tests = []

    def run_test(self, name, test_func):
        """Run a single test"""
        self.tests_run += 1
        print(f"\n🔍 Testing {name}...")
        
        try:
            result = test_func()
            if result:
                self.tests_passed += 1
                print(f"✅ Passed")
                return True
            else:
                print(f"❌ Failed")
                self.failed_tests.append(name)
                return False
        except Exception as e:
            print(f"❌ Failed - Error: {str(e)}")
            self.failed_tests.append(f"{name}: {str(e)}")
            return False

    def test_login(self):
        """Test login and get token"""
        def _test():
            response = requests.post(
                f"{self.base_url}/api/auth/login",
                json={"email": "demo@valuebet.app", "password": "demo1234"},
                timeout=10
            )
            if response.status_code == 200:
                data = response.json()
                self.token = data.get('token')
                print(f"   Token obtained: {self.token[:20]}...")
                return True
            return False
        return self.run_test("Login", _test)

    def test_endpoint(self, name, endpoint):
        """Test an API endpoint"""
        def _test():
            headers = {'Authorization': f'Bearer {self.token}'} if self.token else {}
            response = requests.get(
                f"{self.base_url}{endpoint}",
                headers=headers,
                timeout=10
            )
            print(f"   Status: {response.status_code}")
            return response.status_code == 200
        return self.run_test(name, _test)

    def test_imports(self):
        """Test clean imports for new modules"""
        def _test():
            try:
                # Basketball warehouse
                from services.basketball_intelligence_warehouse import (
                    derive_pattern_keys, lookup_pattern_match, 
                    persist_market_result, attach_pattern_match_to_payload
                )
                print("   ✓ basketball_intelligence_warehouse imports OK")
                
                # MLB settler feedback
                from services.mlb_results_settler import _feed_pattern_memory_from_eval
                print("   ✓ mlb_results_settler imports OK")
                
                # MLB statcast adapter
                from services.mlb_statcast_adapter import get_mlb_advanced_profile
                print("   ✓ mlb_statcast_adapter imports OK")
                
                return True
            except Exception as e:
                print(f"   Import error: {e}")
                return False
        return self.run_test("Clean Imports", _test)

    def test_sport_isolation(self):
        """Test basketball reason codes and collections are isolated from MLB"""
        def _test():
            from services.basketball_intelligence_warehouse import (
                RC_PATTERN_LOW_SAMPLE, RC_PATTERN_MODERATE_BOOST,
                RC_PATTERN_STRONG_BOOST, RC_PATTERN_NEGATIVE_ROI,
                RC_PATTERN_NO_MATCH, RC_WAREHOUSE_DISABLED,
                COLL_TEAM_DAILY, COLL_PLAYER_DAILY, COLL_GAME_SNAPSHOTS,
                COLL_MARKET_RESULTS, COLL_PATTERN_MEMORY,
            )
            
            # Check reason codes start with BBALL_
            reason_codes = [
                RC_PATTERN_LOW_SAMPLE, RC_PATTERN_MODERATE_BOOST,
                RC_PATTERN_STRONG_BOOST, RC_PATTERN_NEGATIVE_ROI,
                RC_PATTERN_NO_MATCH, RC_WAREHOUSE_DISABLED
            ]
            for rc in reason_codes:
                if not rc.startswith("BBALL_"):
                    print(f"   ❌ Reason code {rc} doesn't start with BBALL_")
                    return False
            print("   ✓ All reason codes start with BBALL_")
            
            # Check collections start with bball_
            collections = [
                COLL_TEAM_DAILY, COLL_PLAYER_DAILY, COLL_GAME_SNAPSHOTS,
                COLL_MARKET_RESULTS, COLL_PATTERN_MEMORY
            ]
            for coll in collections:
                if not coll.startswith("bball_"):
                    print(f"   ❌ Collection {coll} doesn't start with bball_")
                    return False
            print("   ✓ All collections start with bball_")
            
            return True
        return self.run_test("Sport Isolation", _test)

    def test_fail_soft(self):
        """Test fail-soft behavior with None inputs"""
        def _test():
            from services.basketball_intelligence_warehouse import (
                derive_pattern_keys, lookup_pattern_match
            )
            
            # derive_pattern_keys with None should return []
            result = derive_pattern_keys(None)
            if result != []:
                print(f"   ❌ derive_pattern_keys(None) returned {result}, expected []")
                return False
            print("   ✓ derive_pattern_keys(None) returns []")
            
            # derive_pattern_keys with empty dict should return []
            result = derive_pattern_keys({})
            if result != []:
                print(f"   ❌ derive_pattern_keys({{}}) returned {result}, expected []")
                return False
            print("   ✓ derive_pattern_keys({}) returns []")
            
            return True
        return self.run_test("Fail-Soft Behavior", _test)

    def test_sample_size_gates(self):
        """Test sample-size gates for basketball patterns"""
        def _test():
            from services.basketball_intelligence_warehouse import (
                _compute_pattern_adjustment,
                PATTERN_MAX_ADJUSTMENT_MODERATE,
                PATTERN_MAX_ADJUSTMENT_STRONG,
                RC_PATTERN_LOW_SAMPLE,
                RC_PATTERN_MODERATE_BOOST,
                RC_PATTERN_STRONG_BOOST,
            )
            
            # Test 1: sample_size < 20 → no adjustment
            adj, codes, warn = _compute_pattern_adjustment(
                sample_size=10, hit_rate=0.8, roi=0.3
            )
            if adj != 0.0:
                print(f"   ❌ Low sample (10) returned adj={adj}, expected 0.0")
                return False
            if RC_PATTERN_LOW_SAMPLE not in codes:
                print(f"   ❌ Low sample codes missing RC_PATTERN_LOW_SAMPLE")
                return False
            print("   ✓ Sample size < 20 returns 0 adjustment")
            
            # Test 2: 20 <= sample_size < 50 → capped at ±5
            adj, codes, _ = _compute_pattern_adjustment(
                sample_size=30, hit_rate=0.62, roi=0.10
            )
            if abs(adj) > PATTERN_MAX_ADJUSTMENT_MODERATE:
                print(f"   ❌ Moderate sample (30) returned adj={adj}, exceeds ±{PATTERN_MAX_ADJUSTMENT_MODERATE}")
                return False
            if RC_PATTERN_MODERATE_BOOST not in codes:
                print(f"   ❌ Moderate sample codes missing RC_PATTERN_MODERATE_BOOST")
                return False
            print(f"   ✓ Sample size 20-49 capped at ±{PATTERN_MAX_ADJUSTMENT_MODERATE}")
            
            # Test 3: sample_size >= 50 with ROI+ → up to ±8
            adj, codes, _ = _compute_pattern_adjustment(
                sample_size=60, hit_rate=0.62, roi=0.15
            )
            if adj <= 0 or adj > PATTERN_MAX_ADJUSTMENT_STRONG:
                print(f"   ❌ Strong sample (60) returned adj={adj}, expected 0 < adj <= {PATTERN_MAX_ADJUSTMENT_STRONG}")
                return False
            if RC_PATTERN_STRONG_BOOST not in codes:
                print(f"   ❌ Strong sample codes missing RC_PATTERN_STRONG_BOOST")
                return False
            print(f"   ✓ Sample size >= 50 with ROI+ returns adjustment <= {PATTERN_MAX_ADJUSTMENT_STRONG}")
            
            return True
        return self.run_test("Sample-Size Gates", _test)

    def test_basketball_patterns(self):
        """Test basketball-specific pattern derivation"""
        def _test():
            from services.basketball_intelligence_warehouse import derive_pattern_keys
            
            # Test HIGH_PACE_OVER_PROFILE
            pp = {
                "home_team_profile": {"pace": 106},
                "away_team_profile": {"pace": 104}
            }
            keys = derive_pattern_keys(pp)
            if "HIGH_PACE_OVER_PROFILE" not in keys:
                print(f"   ❌ HIGH_PACE_OVER_PROFILE not detected")
                return False
            print("   ✓ HIGH_PACE_OVER_PROFILE detected")
            
            # Test LOW_PACE_UNDER_PROFILE
            pp = {
                "home_team_profile": {"pace": 92},
                "away_team_profile": {"pace": 94}
            }
            keys = derive_pattern_keys(pp)
            if "LOW_PACE_UNDER_PROFILE" not in keys:
                print(f"   ❌ LOW_PACE_UNDER_PROFILE not detected")
                return False
            print("   ✓ LOW_PACE_UNDER_PROFILE detected")
            
            # Test STRONG_OFFENSIVE_RATING_EDGE
            pp = {
                "home_team_profile": {"offensive_rating": 120},
                "away_team_profile": {"offensive_rating": 110}
            }
            keys = derive_pattern_keys(pp)
            if "STRONG_OFFENSIVE_RATING_EDGE" not in keys:
                print(f"   ❌ STRONG_OFFENSIVE_RATING_EDGE not detected")
                return False
            print("   ✓ STRONG_OFFENSIVE_RATING_EDGE detected")
            
            return True
        return self.run_test("Basketball Pattern Detection", _test)

def main():
    print("=" * 70)
    print("Backend Testing: Fix 1 (MLB + Basketball) & Fix 2 (Statcast)")
    print("=" * 70)
    
    tester = BackendTester()
    
    # Test 1: Login
    if not tester.test_login():
        print("\n❌ Login failed, stopping API tests")
        return 1
    
    # Test 2: API Endpoints
    tester.test_endpoint("Baseball Picks", "/api/picks/today?sport=baseball")
    tester.test_endpoint("Football Picks", "/api/picks/today?sport=football")
    tester.test_endpoint("Basketball Picks", "/api/picks/today?sport=basketball")
    tester.test_endpoint("Analysis Jobs", "/api/analysis/jobs")
    tester.test_endpoint("Meta Sports", "/api/meta/sports")
    
    # Test 3: Module Imports
    tester.test_imports()
    
    # Test 4: Sport Isolation
    tester.test_sport_isolation()
    
    # Test 5: Fail-Soft
    tester.test_fail_soft()
    
    # Test 6: Sample-Size Gates
    tester.test_sample_size_gates()
    
    # Test 7: Basketball Patterns
    tester.test_basketball_patterns()
    
    # Print results
    print("\n" + "=" * 70)
    print(f"📊 Tests passed: {tester.tests_passed}/{tester.tests_run}")
    if tester.failed_tests:
        print(f"\n❌ Failed tests:")
        for test in tester.failed_tests:
            print(f"   - {test}")
    print("=" * 70)
    
    return 0 if tester.tests_passed == tester.tests_run else 1

if __name__ == "__main__":
    sys.exit(main())
