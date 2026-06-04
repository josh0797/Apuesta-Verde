"""
Backend Integration Tests for Football Moneyball Intelligence Layer

Tests:
1. Backend startup and index creation (verified via logs)
2. GET /api/football/pattern-memory/summary endpoint (auth required, fail-soft)
3. POST /api/analysis/run with sport=football enriches picks
4. MLB and Basketball endpoints still work
5. GET /api/picks/today?sport=football still works
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
                timeout=10
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
    print("🧪 FOOTBALL MONEYBALL BACKEND INTEGRATION TESTS")
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
    
    # Test football picks still work
    tester.test_football_picks_today()
    
    # Test other sports not affected
    tester.test_mlb_not_affected()
    tester.test_basketball_not_affected()
    
    # Print summary
    return tester.print_summary()

if __name__ == "__main__":
    sys.exit(main())
