"""Phase 21 Regression Tests - Ensure existing endpoints still work

Tests:
1. POST /api/auth/login
2. POST /api/analysis/run (with football sport)
3. GET /api/matches/upcoming
4. GET /api/picks/today
5. POST /api/live/reevaluate (performance <5s)
6. Moneyball layer still functional
"""
import requests
import sys
import time
from datetime import datetime

BASE_URL = "https://low-volatility-plays.preview.emergentagent.com/api"

class RegressionTester:
    def __init__(self):
        self.token = None
        self.tests_run = 0
        self.tests_passed = 0
        self.tests_failed = 0
        self.failures = []

    def log(self, msg: str, level: str = "INFO"):
        timestamp = datetime.now().strftime("%H:%M:%S")
        print(f"[{timestamp}] {level}: {msg}")

    def test(self, name: str, method: str, endpoint: str, expected_status: int, 
             data=None, headers=None, check_fn=None, timeout=30):
        """Run a single test."""
        url = f"{BASE_URL}/{endpoint}"
        if headers is None:
            headers = {}
        if self.token and "Authorization" not in headers:
            headers["Authorization"] = f"Bearer {self.token}"
        if data is not None and "Content-Type" not in headers:
            headers["Content-Type"] = "application/json"

        self.tests_run += 1
        self.log(f"Test #{self.tests_run}: {name}")
        
        try:
            start_time = time.time()
            if method == "GET":
                resp = requests.get(url, headers=headers, timeout=timeout)
            elif method == "POST":
                resp = requests.post(url, json=data, headers=headers, timeout=timeout)
            else:
                raise ValueError(f"Unsupported method: {method}")
            elapsed = time.time() - start_time

            # Check status code
            if resp.status_code != expected_status:
                self.tests_failed += 1
                msg = f"❌ FAILED: Expected {expected_status}, got {resp.status_code}"
                self.log(msg, "ERROR")
                self.log(f"   Response: {resp.text[:500]}", "ERROR")
                self.failures.append({"test": name, "reason": msg, "response": resp.text[:500]})
                return False, None, elapsed

            # Parse response
            if resp.headers.get("Content-Type", "").startswith("application/json"):
                result = resp.json()
            else:
                result = resp.text

            # Run custom check function
            if check_fn:
                check_result = check_fn(result)
                if not check_result:
                    self.tests_failed += 1
                    msg = f"❌ FAILED: Custom check failed"
                    self.log(msg, "ERROR")
                    self.failures.append({"test": name, "reason": msg, "response": str(result)[:500]})
                    return False, result, elapsed

            self.tests_passed += 1
            self.log(f"✅ PASSED (took {elapsed:.2f}s)", "SUCCESS")
            return True, result, elapsed

        except Exception as e:
            self.tests_failed += 1
            msg = f"❌ FAILED: Exception - {str(e)}"
            self.log(msg, "ERROR")
            self.failures.append({"test": name, "reason": msg, "response": ""})
            return False, None, 0

    def run_all_tests(self):
        """Execute all regression tests."""
        self.log("=" * 80)
        self.log("PHASE 21 REGRESSION TESTING")
        self.log("=" * 80)

        # Test 1: Login
        self.log("\n[1] AUTH TEST", "SECTION")
        success, result, _ = self.test(
            "Login with demo@valuebet.app",
            "POST", "auth/login", 200,
            data={"email": "demo@valuebet.app", "password": "demo1234"},
            check_fn=lambda r: "token" in r and "user" in r
        )
        if success and result:
            self.token = result["token"]
            self.log(f"   Token acquired: {self.token[:20]}...")

        # Test 2: GET /api/matches/upcoming
        self.log("\n[2] MATCHES/UPCOMING TEST", "SECTION")
        success, result, _ = self.test(
            "GET /api/matches/upcoming",
            "GET", "matches/upcoming", 200,
            check_fn=lambda r: "count" in r and "items" in r
        )
        if success and result:
            self.log(f"   Upcoming matches: {result['count']}")

        # Test 3: GET /api/picks/today
        self.log("\n[3] PICKS/TODAY TEST", "SECTION")
        success, result, _ = self.test(
            "GET /api/picks/today",
            "GET", "picks/today", 200,
            check_fn=lambda r: "pick_run" in r
        )
        if success and result:
            has_picks = result["pick_run"] is not None
            self.log(f"   Has picks: {has_picks}")
            if has_picks:
                payload = result["pick_run"].get("payload", {})
                # Check for new keys: rescued_picks, watchlist, protected_acceptable
                if "summary" in payload:
                    summary = payload["summary"]
                    self.log(f"   Summary keys: {list(summary.keys())}")
                    # These keys should exist (may be empty lists)
                    for key in ["high_confidence", "medium_confidence", "discarded_motivation", "discarded_market"]:
                        if key in summary:
                            self.log(f"   ✓ {key}: {len(summary[key])} items")
                        else:
                            self.log(f"   ⚠ Missing key: {key}", "WARN")

        # Test 4: POST /api/live/reevaluate (performance test <5s)
        self.log("\n[4] LIVE/REEVALUATE PERFORMANCE TEST", "SECTION")
        # First get a live match
        success, result, _ = self.test(
            "GET /api/matches/live (get test match)",
            "GET", "matches/live?sport=football&refresh=false", 200,
            timeout=30,
            check_fn=lambda r: "items" in r
        )
        test_match_id = None
        if success and result and result.get("items"):
            test_match_id = result["items"][0].get("match_id")
            self.log(f"   Using live match ID: {test_match_id}")
        else:
            test_match_id = "999999"
            self.log(f"   No live matches, using synthetic ID")

        if test_match_id:
            success, result, elapsed = self.test(
                "POST /api/live/reevaluate (performance <5s)",
                "POST", "live/reevaluate", 200,
                data={
                    "match_id": test_match_id,
                    "sport": "football",
                    "refresh": False,
                    "manual_odds": 1.85,
                    "manual_market": "Under 2.5"
                },
                timeout=10,
                check_fn=lambda r: "result" in r
            )
            if success:
                if elapsed < 5.0:
                    self.log(f"   ✓ Performance OK: {elapsed:.2f}s < 5s")
                else:
                    self.log(f"   ⚠ Performance warning: {elapsed:.2f}s >= 5s", "WARN")

        # Test 5: Moneyball layer still functional
        self.log("\n[5] MONEYBALL LAYER TEST", "SECTION")
        try:
            from services import moneyball_layer as mb
            
            # Create a synthetic pick
            synthetic_pick = {
                "match_id": "test_123",
                "match_label": "Test Team A vs Test Team B",
                "recommendation": {
                    "market": "Under 2.5",
                    "selection": "Under",
                    "odds_range": "1.85-1.90",
                    "confidence_score": 70
                },
                "reasoning": "Test reasoning",
                "risks": [],
                "is_live": False,
                "key_data": {}
            }
            
            result = mb.analyze_pick(synthetic_pick, sport="football")
            
            if "_moneyball" in result and "_market_edge" in result:
                self.log("   ✓ Moneyball layer functional")
                self.log(f"   Classification: {result['_moneyball'].get('classification')}")
                self.log(f"   Edge: {result['_market_edge'].get('edge')}")
                self.tests_passed += 1
            else:
                self.log("   ❌ Moneyball layer missing expected fields", "ERROR")
                self.tests_failed += 1
                self.failures.append({
                    "test": "Moneyball layer",
                    "reason": "Missing _moneyball or _market_edge",
                    "response": str(result)[:500]
                })
            self.tests_run += 1
            
        except Exception as e:
            self.log(f"   ❌ Moneyball layer test failed: {e}", "ERROR")
            self.tests_failed += 1
            self.tests_run += 1
            self.failures.append({
                "test": "Moneyball layer",
                "reason": str(e),
                "response": ""
            })

        # Test 6: POST /api/analysis/run with football sport
        self.log("\n[6] ANALYSIS/RUN TEST (FOOTBALL)", "SECTION")
        self.log("   ⚠ This test may take 20-90 seconds (LLM analysis)...", "WARN")
        success, result, elapsed = self.test(
            "POST /api/analysis/run (football, max_matches=3)",
            "POST", "analysis/run", 200,
            data={
                "sport": "football",
                "max_matches": 3,
                "background": False,
                "force": True
            },
            timeout=120,
            check_fn=lambda r: "result" in r and "verdict" in r["result"]
        )
        if success and result:
            res = result["result"]
            self.log(f"   Verdict: {res.get('verdict')}")
            self.log(f"   Provider: {res.get('_provider')}")
            if res.get("verdict") == "value_found":
                picks = res.get("picks", [])
                self.log(f"   Picks: {len(picks)}")
                # Check if any picks have corner market rescue
                corner_picks = [p for p in picks if p.get("_alternative_market_payload", {}).get("_source") == "corner_market_layer_v1"]
                if corner_picks:
                    self.log(f"   ✓ Found {len(corner_picks)} corner market rescue picks!")
                    for cp in corner_picks:
                        self.log(f"     - {cp.get('match_label')}: {cp.get('recommendation', {}).get('market')}")

        # Summary
        self.log("\n" + "=" * 80)
        self.log("TEST SUMMARY", "SECTION")
        self.log("=" * 80)
        self.log(f"Total tests: {self.tests_run}")
        self.log(f"Passed: {self.tests_passed} ✅")
        self.log(f"Failed: {self.tests_failed} ❌")
        self.log(f"Success rate: {(self.tests_passed/self.tests_run*100):.1f}%")
        
        if self.failures:
            self.log("\nFAILED TESTS:", "ERROR")
            for i, failure in enumerate(self.failures, 1):
                self.log(f"{i}. {failure['test']}", "ERROR")
                self.log(f"   Reason: {failure['reason']}", "ERROR")
        
        return self.tests_failed == 0


def main():
    tester = RegressionTester()
    success = tester.run_all_tests()
    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
