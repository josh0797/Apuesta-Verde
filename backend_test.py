#!/usr/bin/env python3
"""Backend API Testing for Phase 9, Phase 10, and Objetivo 2."""
import requests
import sys
from datetime import datetime

class MLBBettingAPITester:
    def __init__(self, base_url="https://low-volatility-plays.preview.emergentagent.com"):
        self.base_url = base_url
        self.token = None
        self.tests_run = 0
        self.tests_passed = 0
        self.failed_tests = []

    def run_test(self, name, method, endpoint, expected_status, data=None, headers=None):
        """Run a single API test"""
        url = f"{self.base_url}/{endpoint}"
        if headers is None:
            headers = {'Content-Type': 'application/json'}
        if self.token and 'Authorization' not in headers:
            headers['Authorization'] = f'Bearer {self.token}'

        self.tests_run += 1
        print(f"\n🔍 Testing {name}...")
        
        try:
            if method == 'GET':
                response = requests.get(url, headers=headers, timeout=10)
            elif method == 'POST':
                response = requests.post(url, json=data, headers=headers, timeout=10)
            else:
                response = requests.request(method, url, json=data, headers=headers, timeout=10)

            success = response.status_code == expected_status
            if success:
                self.tests_passed += 1
                print(f"✅ Passed - Status: {response.status_code}")
                try:
                    resp_json = response.json()
                    return True, resp_json
                except:
                    return True, {}
            else:
                print(f"❌ Failed - Expected {expected_status}, got {response.status_code}")
                print(f"   Response: {response.text[:200]}")
                self.failed_tests.append({
                    "name": name,
                    "expected": expected_status,
                    "actual": response.status_code,
                    "response": response.text[:200]
                })
                return False, {}

        except Exception as e:
            print(f"❌ Failed - Error: {str(e)}")
            self.failed_tests.append({
                "name": name,
                "error": str(e)
            })
            return False, {}

    def test_login(self):
        """Test login endpoint"""
        success, response = self.run_test(
            "Login with demo credentials",
            "POST",
            "api/auth/login",
            200,
            data={"email": "demo@valuebet.app", "password": "demo1234"}
        )
        if success and 'token' in response:
            self.token = response['token']
            print(f"   Token obtained: {self.token[:20]}...")
            return True
        return False

    def test_picks_baseball(self):
        """Test baseball picks endpoint"""
        success, response = self.run_test(
            "Get today's baseball picks",
            "GET",
            "api/picks/today?sport=baseball",
            200
        )
        if success:
            picks_count = len(response.get('picks', []))
            print(f"   Found {picks_count} baseball picks (empty list is valid)")
        return success

    def test_picks_football(self):
        """Test football picks endpoint"""
        success, response = self.run_test(
            "Get today's football picks",
            "GET",
            "api/picks/today?sport=football",
            200
        )
        if success:
            picks_count = len(response.get('picks', []))
            print(f"   Found {picks_count} football picks")
        return success

    def test_thestatsapi_health(self):
        """Test TheStatsAPI health endpoint"""
        success, response = self.run_test(
            "TheStatsAPI health check",
            "GET",
            "api/debug/thestatsapi/health?probe=true",
            200
        )
        if success:
            enabled = response.get('enabled', False)
            print(f"   TheStatsAPI enabled: {enabled}")
            if not enabled:
                print("   ⚠️  Warning: TheStatsAPI is not enabled")
        return success

    def test_analysis_jobs(self):
        """Test analysis jobs endpoint"""
        success, response = self.run_test(
            "Get analysis jobs",
            "GET",
            "api/analysis/jobs",
            200
        )
        return success

    def test_meta_sports(self):
        """Test meta sports endpoint"""
        success, response = self.run_test(
            "Get sports metadata",
            "GET",
            "api/meta/sports",
            200
        )
        if success:
            sports = response.get('sports', [])
            if sports:
                if isinstance(sports[0], dict):
                    sport_names = [s.get('name', s.get('id', 'unknown')) for s in sports]
                    print(f"   Available sports: {', '.join(sport_names)}")
                else:
                    print(f"   Available sports: {', '.join(sports)}")
            else:
                print("   Available sports: none")
        return success

def main():
    print("=" * 70)
    print("MLB Betting Intelligence Engine - Phase 9, 10 & Objetivo 2 Testing")
    print("=" * 70)
    
    tester = MLBBettingAPITester()
    
    # Run all tests
    print("\n📋 Running Backend API Tests...")
    
    # Auth test
    if not tester.test_login():
        print("\n❌ Login failed - cannot proceed with authenticated tests")
        print(f"\n📊 Final Results: {tester.tests_passed}/{tester.tests_run} tests passed")
        return 1
    
    # Picks endpoints
    tester.test_picks_baseball()
    tester.test_picks_football()
    
    # Debug/health endpoints
    tester.test_thestatsapi_health()
    
    # Existing endpoints
    tester.test_analysis_jobs()
    tester.test_meta_sports()
    
    # Print summary
    print("\n" + "=" * 70)
    print(f"📊 Test Results: {tester.tests_passed}/{tester.tests_run} tests passed")
    print("=" * 70)
    
    if tester.failed_tests:
        print("\n❌ Failed Tests:")
        for i, test in enumerate(tester.failed_tests, 1):
            print(f"\n{i}. {test.get('name', 'Unknown')}")
            if 'error' in test:
                print(f"   Error: {test['error']}")
            else:
                print(f"   Expected: {test.get('expected')}, Got: {test.get('actual')}")
                if 'response' in test:
                    print(f"   Response: {test['response']}")
    
    success_rate = (tester.tests_passed / tester.tests_run * 100) if tester.tests_run > 0 else 0
    print(f"\n✅ Success Rate: {success_rate:.1f}%")
    
    return 0 if tester.tests_passed == tester.tests_run else 1

if __name__ == "__main__":
    sys.exit(main())
