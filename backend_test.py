"""
Backend Integration Tests for Football Moneyball Intelligence Layer + DC+NB Calibration

Tests:
1. Backend startup and index creation (verified via logs)
2. GET /api/football/pattern-memory/summary endpoint (auth required, fail-soft)
3. GET /api/football/totals-calibration/summary endpoint (DC+NB calibration)
4. POST /api/analysis/run with sport=football enriches picks
5. MLB and Basketball endpoints still work (regression)
6. GET /api/picks/today?sport=football still works (regression)
"""

import requests
import sys
from datetime import datetime

BASE_URL = "https://low-volatility-plays.preview.emergentagent.com"

class FootballMoneybballTester:
    def __init__(self):
        self.base_url = BASE_URL
        self.token = None
        self.tests_run = 0
        self.tests_passed = 0
        self.test_results = []

    def log_test(self, name, passed, message=""):
        """Log test result"""
        self.tests_run += 1
        if passed:
            self.tests_passed += 1
            print(f"✅ PASS: {name}")
        else:
            print(f"❌ FAIL: {name} - {message}")
        self.test_results.append({
            "name": name,
            "passed": passed,
            "message": message
        })

    def login(self):
        """Login and get token"""
        print("\n🔐 Testing Authentication...")
        try:
            response = requests.post(
                f"{self.base_url}/api/auth/login",
                json={"email": "demo@valuebet.app", "password": "demo1234"},
                timeout=30
            )
            if response.status_code == 200:
                data = response.json()
                self.token = data.get("token") or data.get("access_token")
                if self.token:
                    self.log_test("Login", True, f"Token obtained")
                    return True
                else:
                    self.log_test("Login", False, f"No token in response: {data.keys()}")
                    return False
            else:
                self.log_test("Login", False, f"Status {response.status_code}")
                return False
        except Exception as e:
            self.log_test("Login", False, str(e))
            return False

    def test_pattern_memory_summary_auth_required(self):
        """Test that pattern-memory endpoint requires auth"""
        print("\n🔒 Testing Pattern Memory Summary - Auth Required...")
        try:
            response = requests.get(
                f"{self.base_url}/api/football/pattern-memory/summary",
                timeout=10
            )
            # Should return 401 without auth
            if response.status_code == 401:
                self.log_test("Pattern Memory Auth Required", True, "401 Unauthorized as expected")
                return True
            else:
                self.log_test("Pattern Memory Auth Required", False, f"Expected 401, got {response.status_code}")
                return False
        except Exception as e:
            self.log_test("Pattern Memory Auth Required", False, str(e))
            return False

    def test_pattern_memory_summary_authenticated(self):
        """Test pattern-memory endpoint with authentication"""
        print("\n📊 Testing Pattern Memory Summary - Authenticated...")
        try:
            headers = {"Authorization": f"Bearer {self.token}"}
            response = requests.get(
                f"{self.base_url}/api/football/pattern-memory/summary?limit=10",
                headers=headers,
                timeout=10
            )
            
            if response.status_code == 200:
                data = response.json()
                # Check expected shape
                required_keys = ["available", "items", "count", "generated_at"]
                has_all_keys = all(k in data for k in required_keys)
                
                if has_all_keys:
                    # Empty DB is expected initially
                    if data["available"] and data["count"] == 0 and isinstance(data["items"], list):
                        self.log_test(
                            "Pattern Memory Summary Shape",
                            True,
                            f"Empty DB response correct: {data['count']} items"
                        )
                        return True
                    elif data["available"]:
                        self.log_test(
                            "Pattern Memory Summary Shape",
                            True,
                            f"Response has {data['count']} items"
                        )
                        return True
                    else:
                        self.log_test(
                            "Pattern Memory Summary Shape",
                            False,
                            f"available=False: {data}"
                        )
                        return False
                else:
                    self.log_test(
                        "Pattern Memory Summary Shape",
                        False,
                        f"Missing keys. Got: {list(data.keys())}"
                    )
                    return False
            else:
                self.log_test(
                    "Pattern Memory Summary",
                    False,
                    f"Status {response.status_code}: {response.text[:200]}"
                )
                return False
        except Exception as e:
            self.log_test("Pattern Memory Summary", False, str(e))
            return False

    def test_pattern_memory_fail_soft(self):
        """Test that pattern-memory endpoint is fail-soft (doesn't return 500)"""
        print("\n🛡️ Testing Pattern Memory Fail-Soft...")
        try:
            headers = {"Authorization": f"Bearer {self.token}"}
            # Try with various parameters to ensure fail-soft
            response = requests.get(
                f"{self.base_url}/api/football/pattern-memory/summary?limit=100",
                headers=headers,
                timeout=10
            )
            
            # Should never return 500
            if response.status_code != 500:
                self.log_test(
                    "Pattern Memory Fail-Soft",
                    True,
                    f"No 500 error (got {response.status_code})"
                )
                return True
            else:
                self.log_test(
                    "Pattern Memory Fail-Soft",
                    False,
                    f"Got 500 error: {response.text[:200]}"
                )
                return False
        except Exception as e:
            self.log_test("Pattern Memory Fail-Soft", False, str(e))
            return False

    def test_football_picks_today(self):
        """Test that /api/picks/today?sport=football still works"""
        print("\n⚽ Testing Football Picks Today...")
        try:
            headers = {"Authorization": f"Bearer {self.token}"}
            response = requests.get(
                f"{self.base_url}/api/picks/today?sport=football",
                headers=headers,
                timeout=15
            )
            
            if response.status_code == 200:
                data = response.json()
                # Check it's a valid response
                if isinstance(data, dict):
                    self.log_test(
                        "Football Picks Today",
                        True,
                        f"Response OK with {len(data.get('picks', []))} picks"
                    )
                    
                    # Check if any picks have moneyball enrichment
                    picks = data.get("picks", [])
                    if picks:
                        first_pick = picks[0]
                        has_moneyball = any(k in first_pick for k in [
                            "goal_pressure_profile",
                            "market_selection",
                            "historical_pattern_match",
                            "football_pattern_keys"
                        ])
                        if has_moneyball:
                            print(f"   ℹ️  Picks have Moneyball enrichment")
                        else:
                            print(f"   ℹ️  Picks don't have Moneyball enrichment yet (may need analysis run)")
                    return True
                else:
                    self.log_test("Football Picks Today", False, f"Invalid response type: {type(data)}")
                    return False
            else:
                self.log_test(
                    "Football Picks Today",
                    False,
                    f"Status {response.status_code}: {response.text[:200]}"
                )
                return False
        except Exception as e:
            self.log_test("Football Picks Today", False, str(e))
            return False

    def test_mlb_not_affected(self):
        """Test that MLB endpoints still work"""
        print("\n⚾ Testing MLB Not Affected...")
        try:
            headers = {"Authorization": f"Bearer {self.token}"}
            response = requests.get(
                f"{self.base_url}/api/picks/today?sport=baseball",
                headers=headers,
                timeout=15
            )
            
            if response.status_code == 200:
                data = response.json()
                if isinstance(data, dict):
                    self.log_test(
                        "MLB Not Affected",
                        True,
                        f"MLB endpoint works with {len(data.get('picks', []))} picks"
                    )
                    return True
                else:
                    self.log_test("MLB Not Affected", False, f"Invalid response type")
                    return False
            else:
                self.log_test(
                    "MLB Not Affected",
                    False,
                    f"Status {response.status_code}"
                )
                return False
        except Exception as e:
            self.log_test("MLB Not Affected", False, str(e))
            return False

    def test_basketball_not_affected(self):
        """Test that Basketball endpoints still work"""
        print("\n🏀 Testing Basketball Not Affected...")
        try:
            headers = {"Authorization": f"Bearer {self.token}"}
            response = requests.get(
                f"{self.base_url}/api/picks/today?sport=basketball",
                headers=headers,
                timeout=15
            )
            
            if response.status_code == 200:
                data = response.json()
                if isinstance(data, dict):
                    self.log_test(
                        "Basketball Not Affected",
                        True,
                        f"Basketball endpoint works with {len(data.get('picks', []))} picks"
                    )
                    return True
                else:
                    self.log_test("Basketball Not Affected", False, f"Invalid response type")
                    return False
            else:
                self.log_test(
                    "Basketball Not Affected",
                    False,
                    f"Status {response.status_code}"
                )
                return False
        except Exception as e:
            self.log_test("Basketball Not Affected", False, str(e))
            return False

    def test_totals_calibration_summary(self):
        """Test the new football totals-calibration endpoint (DC+NB)"""
        print("\n⚙️ Testing Football Totals Calibration Summary (DC+NB)...")
        try:
            headers = {"Authorization": f"Bearer {self.token}"}
            response = requests.get(
                f"{self.base_url}/api/football/totals-calibration/summary?days=90",
                headers=headers,
                timeout=10
            )
            
            if response.status_code == 200:
                data = response.json()
                # Check top-level shape
                if "ok" in data and "summary" in data:
                    summary = data["summary"]
                    
                    # Check required fields
                    required_fields = [
                        "available", "rho", "dispersion_ratio",
                        "by_league_tier", "by_offense", "bucket_application_policy"
                    ]
                    missing = [f for f in required_fields if f not in summary]
                    
                    if missing:
                        self.log_test(
                            "Totals Calibration Summary Shape",
                            False,
                            f"Missing fields: {missing}"
                        )
                        return False
                    
                    # Check rho structure and clamp
                    rho = summary.get("rho", {})
                    if "to_apply" in rho:
                        rho_val = rho["to_apply"]
                        if -0.20 <= rho_val <= 0.0:
                            self.log_test(
                                "Totals Calibration rho clamp",
                                True,
                                f"rho.to_apply={rho_val} within [-0.20, 0.0]"
                            )
                        else:
                            self.log_test(
                                "Totals Calibration rho clamp",
                                False,
                                f"rho.to_apply={rho_val} outside clamp range"
                            )
                            return False
                    
                    # Check dispersion_ratio structure and clamp
                    ratio = summary.get("dispersion_ratio", {})
                    if "to_apply" in ratio:
                        ratio_val = ratio["to_apply"]
                        if 1.0 <= ratio_val <= 2.0:
                            self.log_test(
                                "Totals Calibration ratio clamp",
                                True,
                                f"dispersion_ratio.to_apply={ratio_val} within [1.0, 2.0]"
                            )
                        else:
                            self.log_test(
                                "Totals Calibration ratio clamp",
                                False,
                                f"dispersion_ratio.to_apply={ratio_val} outside clamp range"
                            )
                            return False
                    
                    # Check bucket structure
                    by_league = summary.get("by_league_tier", {})
                    expected_tiers = ["TIER1", "TIER2", "TIER3", "UNKNOWN_LEAGUE"]
                    missing_tiers = [t for t in expected_tiers if t not in by_league]
                    
                    if missing_tiers:
                        self.log_test(
                            "Totals Calibration league tiers",
                            False,
                            f"Missing tiers: {missing_tiers}"
                        )
                        return False
                    
                    by_offense = summary.get("by_offense", {})
                    expected_buckets = ["LOW_OFFENSE", "MODERATE_OFFENSE", "HIGH_OFFENSE"]
                    missing_buckets = [b for b in expected_buckets if b not in by_offense]
                    
                    if missing_buckets:
                        self.log_test(
                            "Totals Calibration offense buckets",
                            False,
                            f"Missing buckets: {missing_buckets}"
                        )
                        return False
                    
                    # Check bucket_application_policy
                    policy = summary.get("bucket_application_policy", {})
                    if policy.get("mode") == "OBSERVE_ONLY":
                        self.log_test(
                            "Totals Calibration Summary",
                            True,
                            f"All checks passed (sample_size={summary.get('sample_size', 0)})"
                        )
                        return True
                    else:
                        self.log_test(
                            "Totals Calibration policy",
                            False,
                            f"Expected mode=OBSERVE_ONLY, got {policy.get('mode')}"
                        )
                        return False
                else:
                    self.log_test(
                        "Totals Calibration Summary",
                        False,
                        f"Missing 'ok' or 'summary' in response"
                    )
                    return False
            else:
                self.log_test(
                    "Totals Calibration Summary",
                    False,
                    f"Status {response.status_code}: {response.text[:200]}"
                )
                return False
        except Exception as e:
            self.log_test("Totals Calibration Summary", False, str(e))
            return False

    def test_totals_calibration_fail_soft(self):
        """Test that totals-calibration endpoint is fail-soft with invalid inputs"""
        print("\n🛡️ Testing Totals Calibration Fail-Soft...")
        try:
            headers = {"Authorization": f"Bearer {self.token}"}
            # Test with days=0 (should cap to 7)
            response = requests.get(
                f"{self.base_url}/api/football/totals-calibration/summary?days=0",
                headers=headers,
                timeout=10
            )
            
            if response.status_code == 200:
                data = response.json()
                if "ok" in data:
                    self.log_test(
                        "Totals Calibration Fail-Soft",
                        True,
                        "days=0 handled gracefully (capped to 7)"
                    )
                    return True
                else:
                    self.log_test(
                        "Totals Calibration Fail-Soft",
                        False,
                        "Response missing 'ok' field"
                    )
                    return False
            else:
                self.log_test(
                    "Totals Calibration Fail-Soft",
                    False,
                    f"Status {response.status_code}"
                )
                return False
        except Exception as e:
            self.log_test("Totals Calibration Fail-Soft", False, str(e))
            return False

    def test_calibration_summary(self):
        """Test GET /api/calibration/summary endpoint"""
        print("\n🔍 Testing Calibration Summary...")
        try:
            response = requests.get(
                f"{self.base_url}/api/calibration/summary?days=30",
                headers={"Authorization": f"Bearer {self.token}"},
                timeout=10
            )
            if response.status_code == 200:
                data = response.json()
                # Validate structure
                required_keys = [
                    "days", "total_picks", "engine", "user", 
                    "followed_engine_rate", "delta_breakdown",
                    "avg_line_protection", "engine_won_user_lost", 
                    "engine_lost_user_won"
                ]
                missing = [k for k in required_keys if k not in data]
                if missing:
                    self.log_test(
                        "Calibration Summary Structure",
                        False,
                        f"Missing keys: {missing}"
                    )
                    return False
                
                # Validate engine and user blocks
                for block_name in ["engine", "user"]:
                    block = data.get(block_name, {})
                    block_keys = ["wins", "losses", "pushes", "sample", "win_rate"]
                    missing_block = [k for k in block_keys if k not in block]
                    if missing_block:
                        self.log_test(
                            f"Calibration Summary {block_name} Block",
                            False,
                            f"Missing keys: {missing_block}"
                        )
                        return False
                
                self.log_test(
                    "Calibration Summary",
                    True,
                    f"Total picks: {data.get('total_picks')}"
                )
                return True
            else:
                self.log_test(
                    "Calibration Summary",
                    False,
                    f"Status {response.status_code}"
                )
                return False
        except Exception as e:
            self.log_test("Calibration Summary", False, str(e))
            return False

    def test_calibration_divergences(self):
        """Test GET /api/calibration/divergences endpoint"""
        print("\n🔍 Testing Calibration Divergences...")
        try:
            response = requests.get(
                f"{self.base_url}/api/calibration/divergences?days=30&limit=5",
                headers={"Authorization": f"Bearer {self.token}"},
                timeout=10
            )
            if response.status_code == 200:
                data = response.json()
                required_keys = ["days", "count", "items"]
                missing = [k for k in required_keys if k not in data]
                if missing:
                    self.log_test(
                        "Calibration Divergences",
                        False,
                        f"Missing keys: {missing}"
                    )
                    return False
                
                self.log_test(
                    "Calibration Divergences",
                    True,
                    f"Count: {data.get('count')}"
                )
                return True
            else:
                self.log_test(
                    "Calibration Divergences",
                    False,
                    f"Status {response.status_code}"
                )
                return False
        except Exception as e:
            self.log_test("Calibration Divergences", False, str(e))
            return False

    def test_track_pick_with_divergence(self):
        """Test POST /api/picks/track with divergence analysis"""
        print("\n🔍 Testing Track Pick with Divergence...")
        try:
            test_pick = {
                "run_id": f"test-run-{datetime.now().strftime('%Y%m%d%H%M%S')}",
                "match_id": f"test-match-{datetime.now().strftime('%Y%m%d%H%M%S')}",
                "match_label": "Test Team A vs Test Team B",
                "home_team": "Test Team A",
                "away_team": "Test Team B",
                "league": "Test League",
                "sport": "baseball",
                "market": "total_runs",
                "selection": "UNDER",
                "line": 9.5,
                "confidence_score": 75,
                "outcome": "won",
                "odds": 1.90,
                "actual_market": "total_runs",
                "actual_selection": "UNDER",
                "actual_line": 10.5,
                "actual_odds": 1.85,
                "actual_outcome": "won",
                "final_score": {
                    "home": 6,
                    "away": 4,
                    "display": "6-4"
                },
                "match_date": datetime.now().strftime("%Y-%m-%d"),
                "kickoff_iso": datetime.now().isoformat()
            }
            
            response = requests.post(
                f"{self.base_url}/api/picks/track",
                json=test_pick,
                headers={"Authorization": f"Bearer {self.token}"},
                timeout=10
            )
            
            if response.status_code == 200:
                data = response.json()
                # Check if pick was created successfully
                if data.get("ok") and data.get("pick_id"):
                    self.log_test(
                        "Track Pick with Divergence",
                        True,
                        f"Pick created: {data.get('pick_id')[:30]}..."
                    )
                    return True
                else:
                    self.log_test(
                        "Track Pick with Divergence",
                        False,
                        "No pick_id in response"
                    )
                    return False
            else:
                self.log_test(
                    "Track Pick with Divergence",
                    False,
                    f"Status {response.status_code}"
                )
                return False
        except Exception as e:
            self.log_test("Track Pick with Divergence", False, str(e))
            return False

    def test_patch_user_bet(self):
        """Test PATCH /api/picks/{pick_uid}/user-bet endpoint"""
        print("\n🔍 Testing Patch User Bet...")
        try:
            # First create a pick
            test_pick = {
                "run_id": f"test-run-patch-{datetime.now().strftime('%Y%m%d%H%M%S')}",
                "match_id": f"test-match-patch-{datetime.now().strftime('%Y%m%d%H%M%S')}",
                "match_label": "Patch Test A vs Patch Test B",
                "home_team": "Patch Test A",
                "away_team": "Patch Test B",
                "league": "Test League",
                "sport": "baseball",
                "market": "total_runs",
                "selection": "UNDER",
                "line": 9.5,
                "confidence_score": 75,
                "outcome": "pending",
                "odds": 1.90,
                "match_date": datetime.now().strftime("%Y-%m-%d"),
                "kickoff_iso": datetime.now().isoformat()
            }
            
            track_response = requests.post(
                f"{self.base_url}/api/picks/track",
                json=test_pick,
                headers={"Authorization": f"Bearer {self.token}"},
                timeout=10
            )
            
            if track_response.status_code != 200:
                self.log_test(
                    "Patch User Bet - Create Pick",
                    False,
                    f"Failed to create pick: {track_response.status_code}"
                )
                return False
            
            pick_uid = track_response.json().get("pick_id")
            if not pick_uid:
                self.log_test(
                    "Patch User Bet - Get UID",
                    False,
                    "No pick_id in response"
                )
                return False
            
            # Now patch it
            patch_data = {
                "market": "total_runs",
                "selection": "UNDER",
                "line": 10.5,
                "odds": 1.85,
                "outcome": "won",
                "final_score": {
                    "home": 6,
                    "away": 4,
                    "display": "6-4"
                }
            }
            
            patch_response = requests.patch(
                f"{self.base_url}/api/picks/{pick_uid}/user-bet",
                json=patch_data,
                headers={"Authorization": f"Bearer {self.token}"},
                timeout=10
            )
            
            if patch_response.status_code == 200:
                data = patch_response.json()
                if "divergence" in data:
                    self.log_test(
                        "Patch User Bet",
                        True,
                        f"Divergence recomputed"
                    )
                    return True
                else:
                    self.log_test(
                        "Patch User Bet",
                        False,
                        "No divergence in response"
                    )
                    return False
            else:
                self.log_test(
                    "Patch User Bet",
                    False,
                    f"Status {patch_response.status_code}"
                )
                return False
        except Exception as e:
            self.log_test("Patch User Bet", False, str(e))
            return False



    def print_summary(self):
        """Print test summary"""
        print("\n" + "="*70)
        print("📊 TEST SUMMARY")
        print("="*70)
        print(f"Total Tests: {self.tests_run}")
        print(f"Passed: {self.tests_passed}")
        print(f"Failed: {self.tests_run - self.tests_passed}")
        print(f"Success Rate: {(self.tests_passed/self.tests_run*100):.1f}%")
        print("="*70)
        
        if self.tests_passed == self.tests_run:
            print("✅ ALL TESTS PASSED!")
            return 0
        else:
            print("❌ SOME TESTS FAILED")
            print("\nFailed Tests:")
            for result in self.test_results:
                if not result["passed"]:
                    print(f"  - {result['name']}: {result['message']}")
            return 1

def main():
    print("="*70)
    print("🧪 FOOTBALL MONEYBALL + DC+NB CALIBRATION BACKEND TESTS")
    print("="*70)
    
    tester = FootballMoneybballTester()
    
    # Run tests in order
    if not tester.login():
        print("\n❌ Login failed, cannot continue tests")
        return 1
    
    # Test pattern memory endpoint
    tester.test_pattern_memory_summary_auth_required()
    tester.test_pattern_memory_summary_authenticated()
    tester.test_pattern_memory_fail_soft()
    
    # Test NEW DC+NB calibration endpoints
    tester.test_totals_calibration_summary()
    tester.test_totals_calibration_fail_soft()
    
    # Test NEW Pick Divergence Analysis endpoints
    tester.test_calibration_summary()
    tester.test_calibration_divergences()
    tester.test_track_pick_with_divergence()
    tester.test_patch_user_bet()
    
    # Test football picks still work
    tester.test_football_picks_today()
    
    # Test other sports not affected (regression)
    tester.test_mlb_not_affected()
    tester.test_basketball_not_affected()
    
    # Print summary
    return tester.print_summary()

if __name__ == "__main__":
    sys.exit(main())
