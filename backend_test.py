"""
Backend API Testing for Value Bet Intelligence v2
Historical Learning v2 + Decision Intelligence + Auto-promotion
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
        self.test_results = []

    def log_result(self, test_name, passed, details=""):
        """Log test result"""
        self.tests_run += 1
        if passed:
            self.tests_passed += 1
            print(f"✅ PASS: {test_name}")
        else:
            print(f"❌ FAIL: {test_name}")
        if details:
            print(f"   {details}")
        self.test_results.append({
            "test": test_name,
            "passed": passed,
            "details": details
        })

    def login(self, email="demo@valuebet.app", password="demo1234"):
        """Login and get token"""
        print(f"\n🔐 Logging in as {email}...")
        try:
            response = requests.post(
                f"{self.base_url}/api/auth/login",
                json={"email": email, "password": password},
                timeout=10
            )
            if response.status_code == 200:
                data = response.json()
                self.token = data.get("token")
                if self.token:
                    print(f"✅ Login successful, token obtained")
                    return True
                else:
                    print(f"❌ Login response missing token: {data}")
                    return False
            else:
                print(f"❌ Login failed: {response.status_code} - {response.text}")
                return False
        except Exception as e:
            print(f"❌ Login error: {str(e)}")
            return False

    def get_headers(self):
        """Get auth headers"""
        return {
            'Content-Type': 'application/json',
            'Authorization': f'Bearer {self.token}'
        }

    def test_auto_promotion_heavy_sync(self):
        """Test auto-promotion: background=false + max_matches=8 should auto-promote to background"""
        print("\n🧪 TEST: Auto-promotion (max_matches=8, background=false)")
        try:
            start = time.time()
            response = requests.post(
                f"{self.base_url}/api/analysis/run",
                json={
                    "refresh": False,  # Don't refresh to keep it fast
                    "include_live": False,
                    "max_matches": 8,
                    "sport": "football",
                    "background": False  # Request SYNC but should auto-promote
                },
                headers=self.get_headers(),
                timeout=15
            )
            elapsed = time.time() - start
            
            if response.status_code == 200:
                data = response.json()
                has_auto_promoted = data.get("_auto_promoted") == True
                has_reason = "_auto_promoted_reason" in data
                has_job_id = "job_id" in data
                is_fast = elapsed < 5.0  # Should return immediately
                
                if has_auto_promoted and has_reason and has_job_id and is_fast:
                    self.log_result(
                        "Auto-promotion (max_matches=8)",
                        True,
                        f"✓ Returned 200 in {elapsed:.2f}s with _auto_promoted=true, job_id={data.get('job_id')}"
                    )
                    return True
                else:
                    self.log_result(
                        "Auto-promotion (max_matches=8)",
                        False,
                        f"Missing fields: _auto_promoted={has_auto_promoted}, _auto_promoted_reason={has_reason}, job_id={has_job_id}, fast={is_fast} ({elapsed:.2f}s)"
                    )
                    return False
            else:
                self.log_result(
                    "Auto-promotion (max_matches=8)",
                    False,
                    f"Expected 200, got {response.status_code}: {response.text[:200]}"
                )
                return False
        except Exception as e:
            self.log_result("Auto-promotion (max_matches=8)", False, f"Exception: {str(e)}")
            return False

    def test_normal_sync(self):
        """Test normal SYNC: background=false + max_matches=3 should NOT auto-promote"""
        print("\n🧪 TEST: Normal SYNC (max_matches=3, background=false)")
        try:
            response = requests.post(
                f"{self.base_url}/api/analysis/run",
                json={
                    "refresh": False,
                    "include_live": False,
                    "max_matches": 3,
                    "sport": "football",
                    "background": False
                },
                headers=self.get_headers(),
                timeout=120  # Sync can take longer
            )
            
            if response.status_code == 200:
                data = response.json()
                has_auto_promoted = data.get("_auto_promoted") == True
                has_result = "result" in data or "pick_run_id" in data
                
                if not has_auto_promoted and has_result:
                    self.log_result(
                        "Normal SYNC (max_matches=3)",
                        True,
                        f"✓ Returned full result without auto-promotion"
                    )
                    return True
                else:
                    self.log_result(
                        "Normal SYNC (max_matches=3)",
                        False,
                        f"Unexpected: _auto_promoted={has_auto_promoted}, has_result={has_result}"
                    )
                    return False
            else:
                self.log_result(
                    "Normal SYNC (max_matches=3)",
                    False,
                    f"Expected 200, got {response.status_code}: {response.text[:200]}"
                )
                return False
        except Exception as e:
            self.log_result("Normal SYNC (max_matches=3)", False, f"Exception: {str(e)}")
            return False

    def test_background_mode(self):
        """Test background mode: background=true should return job_id immediately"""
        print("\n🧪 TEST: Background mode (background=true)")
        try:
            start = time.time()
            response = requests.post(
                f"{self.base_url}/api/analysis/run",
                json={
                    "refresh": False,
                    "include_live": False,
                    "max_matches": 5,
                    "sport": "football",
                    "background": True
                },
                headers=self.get_headers(),
                timeout=10
            )
            elapsed = time.time() - start
            
            if response.status_code == 200:
                data = response.json()
                has_job_id = "job_id" in data
                has_status = data.get("status") in ["queued", "running"]
                is_fast = elapsed < 3.0
                
                if has_job_id and has_status and is_fast:
                    self.log_result(
                        "Background mode",
                        True,
                        f"✓ Returned job_id={data.get('job_id')} in {elapsed:.2f}s"
                    )
                    return True
                else:
                    self.log_result(
                        "Background mode",
                        False,
                        f"Missing fields: job_id={has_job_id}, status={has_status}, fast={is_fast}"
                    )
                    return False
            else:
                self.log_result(
                    "Background mode",
                    False,
                    f"Expected 200, got {response.status_code}"
                )
                return False
        except Exception as e:
            self.log_result("Background mode", False, f"Exception: {str(e)}")
            return False

    def test_stats_timeline_fields(self):
        """Test /api/stats/timeline returns new fields: market, selection, odds, league, confidence_score"""
        print("\n🧪 TEST: /api/stats/timeline new fields")
        try:
            response = requests.get(
                f"{self.base_url}/api/stats/timeline",
                headers=self.get_headers(),
                timeout=10
            )
            
            if response.status_code == 200:
                data = response.json()
                timeline = data.get("timeline", [])
                
                if len(timeline) == 0:
                    self.log_result(
                        "Timeline new fields",
                        False,
                        "No timeline entries found (demo user should have data)"
                    )
                    return False
                
                # Check first entry has new fields
                entry = timeline[0]
                has_market = "market" in entry
                has_selection = "selection" in entry
                has_odds = "odds" in entry
                has_league = "league" in entry
                has_confidence = "confidence_score" in entry
                has_outcome = "outcome" in entry
                has_winrate = "win_rate" in entry
                
                all_fields = has_market and has_selection and has_odds and has_league and has_confidence and has_outcome and has_winrate
                
                if all_fields:
                    self.log_result(
                        "Timeline new fields",
                        True,
                        f"✓ All new fields present in {len(timeline)} entries"
                    )
                    return True
                else:
                    self.log_result(
                        "Timeline new fields",
                        False,
                        f"Missing fields: market={has_market}, selection={has_selection}, odds={has_odds}, league={has_league}, confidence={has_confidence}"
                    )
                    return False
            else:
                self.log_result(
                    "Timeline new fields",
                    False,
                    f"Expected 200, got {response.status_code}"
                )
                return False
        except Exception as e:
            self.log_result("Timeline new fields", False, f"Exception: {str(e)}")
            return False

    def test_stats_timeline_backward_compat(self):
        """Test /api/stats/timeline handles BOTH schemas: outcome={won,lost} AND result={win,lose}"""
        print("\n🧪 TEST: /api/stats/timeline backward compatibility")
        try:
            response = requests.get(
                f"{self.base_url}/api/stats/timeline",
                headers=self.get_headers(),
                timeout=10
            )
            
            if response.status_code == 200:
                data = response.json()
                timeline = data.get("timeline", [])
                
                if len(timeline) == 0:
                    self.log_result(
                        "Timeline backward compat",
                        False,
                        "No timeline entries to test"
                    )
                    return False
                
                # All entries should have normalized 'outcome' field
                all_have_outcome = all("outcome" in e for e in timeline)
                all_outcomes_valid = all(e.get("outcome") in ["won", "lost"] for e in timeline)
                
                if all_have_outcome and all_outcomes_valid:
                    self.log_result(
                        "Timeline backward compat",
                        True,
                        f"✓ All {len(timeline)} entries have normalized outcome field"
                    )
                    return True
                else:
                    self.log_result(
                        "Timeline backward compat",
                        False,
                        f"Some entries missing or invalid outcome: all_have={all_have_outcome}, all_valid={all_outcomes_valid}"
                    )
                    return False
            else:
                self.log_result(
                    "Timeline backward compat",
                    False,
                    f"Expected 200, got {response.status_code}"
                )
                return False
        except Exception as e:
            self.log_result("Timeline backward compat", False, f"Exception: {str(e)}")
            return False

    def test_learning_stats_regression(self):
        """Test /api/learning/stats still works (regression test)"""
        print("\n🧪 TEST: /api/learning/stats regression")
        try:
            response = requests.get(
                f"{self.base_url}/api/learning/stats",
                headers=self.get_headers(),
                timeout=10
            )
            
            if response.status_code == 200:
                data = response.json()
                patterns = data.get("patterns", [])
                total_tracked = data.get("total_tracked", 0)
                
                # Demo user should have some patterns
                has_patterns = len(patterns) > 0
                has_total = total_tracked > 0
                
                if has_patterns and has_total:
                    self.log_result(
                        "Learning stats regression",
                        True,
                        f"✓ Returned {len(patterns)} patterns, total_tracked={total_tracked}"
                    )
                    return True
                else:
                    self.log_result(
                        "Learning stats regression",
                        False,
                        f"Expected patterns and total_tracked > 0, got patterns={len(patterns)}, total={total_tracked}"
                    )
                    return False
            else:
                self.log_result(
                    "Learning stats regression",
                    False,
                    f"Expected 200, got {response.status_code}"
                )
                return False
        except Exception as e:
            self.log_result("Learning stats regression", False, f"Exception: {str(e)}")
            return False

    def test_saved_views_crud(self):
        """Test /api/profile/saved-views CRUD operations (regression)"""
        print("\n🧪 TEST: Saved views CRUD regression")
        try:
            # GET
            response = requests.get(
                f"{self.base_url}/api/profile/saved-views",
                headers=self.get_headers(),
                timeout=10
            )
            if response.status_code != 200:
                self.log_result("Saved views GET", False, f"GET failed: {response.status_code}")
                return False
            
            # POST
            response = requests.post(
                f"{self.base_url}/api/profile/saved-views",
                json={
                    "name": f"Test View {int(time.time())}",
                    "filters": {"minConfidence": 70},
                    "sport": "football"
                },
                headers=self.get_headers(),
                timeout=10
            )
            if response.status_code != 200:
                self.log_result("Saved views POST", False, f"POST failed: {response.status_code}")
                return False
            
            view_id = response.json().get("id")
            if not view_id:
                self.log_result("Saved views POST", False, "POST response missing id")
                return False
            
            # PATCH
            response = requests.patch(
                f"{self.base_url}/api/profile/saved-views/{view_id}",
                json={"name": "Updated Test View"},
                headers=self.get_headers(),
                timeout=10
            )
            if response.status_code != 200:
                self.log_result("Saved views PATCH", False, f"PATCH failed: {response.status_code}")
                return False
            
            # DELETE
            response = requests.delete(
                f"{self.base_url}/api/profile/saved-views/{view_id}",
                headers=self.get_headers(),
                timeout=10
            )
            if response.status_code != 200:
                self.log_result("Saved views DELETE", False, f"DELETE failed: {response.status_code}")
                return False
            
            self.log_result(
                "Saved views CRUD",
                True,
                "✓ GET/POST/PATCH/DELETE all working"
            )
            return True
            
        except Exception as e:
            self.log_result("Saved views CRUD", False, f"Exception: {str(e)}")
            return False

    def run_all_tests(self):
        """Run all backend tests"""
        print("=" * 70)
        print("VALUE BET INTELLIGENCE v2 - BACKEND API TESTS")
        print("Historical Learning v2 + Decision Intelligence + Auto-promotion")
        print("=" * 70)
        
        if not self.login():
            print("\n❌ Login failed, cannot proceed with tests")
            return False
        
        # Run all tests
        self.test_auto_promotion_heavy_sync()
        self.test_normal_sync()
        self.test_background_mode()
        self.test_stats_timeline_fields()
        self.test_stats_timeline_backward_compat()
        self.test_learning_stats_regression()
        self.test_saved_views_crud()
        
        # Summary
        print("\n" + "=" * 70)
        print(f"📊 BACKEND TEST SUMMARY")
        print("=" * 70)
        print(f"Tests run: {self.tests_run}")
        print(f"Tests passed: {self.tests_passed}")
        print(f"Tests failed: {self.tests_run - self.tests_passed}")
        print(f"Success rate: {(self.tests_passed / self.tests_run * 100):.1f}%")
        print("=" * 70)
        
        return self.tests_passed == self.tests_run

def main():
    tester = ValueBetAPITester()
    success = tester.run_all_tests()
    return 0 if success else 1

if __name__ == "__main__":
    sys.exit(main())
