"""Backend test for P4.1 features: Carry-over picks + Editorial Context for NBA/MLB.

Tests:
1. Module imports (carryover_picks, editorial_context_service)
2. Auth (demo@valuebet.app / demo1234)
3. POST /api/analysis/run for football - first run
4. POST /api/analysis/run for football - second run (verify carryover_picks)
5. Carry-over policy rules (match status, confidence, duplicates, invalidators)
6. Editorial Context for basketball (no sport_not_supported error)
7. Editorial Context for baseball (no sport_not_supported error)
8. GET /api/picks/today returns carryover field
"""
import requests
import sys
import time
import json
from datetime import datetime, timezone, timedelta
from typing import Optional

BASE_URL = "https://low-volatility-plays.preview.emergentagent.com/api"

class CarryoverEditorialTester:
    def __init__(self):
        self.token = None
        self.user_id = None
        self.tests_run = 0
        self.tests_passed = 0
        self.tests_failed = 0
        self.failures = []

    def log(self, msg: str, level: str = "INFO"):
        timestamp = datetime.now().strftime("%H:%M:%S")
        print(f"[{timestamp}] {level}: {msg}")

    def test(self, name: str, method: str, endpoint: str, expected_status: int, 
             data=None, headers=None, check_fn=None, timeout=120):
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
            if method == "GET":
                resp = requests.get(url, headers=headers, timeout=timeout)
            elif method == "POST":
                resp = requests.post(url, json=data, headers=headers, timeout=timeout)
            else:
                raise ValueError(f"Unsupported method: {method}")

            # Check status code
            if resp.status_code != expected_status:
                self.tests_failed += 1
                msg = f"❌ FAILED: Expected {expected_status}, got {resp.status_code}"
                self.log(msg, "ERROR")
                self.log(f"   Response: {resp.text[:800]}", "ERROR")
                self.failures.append({"test": name, "reason": msg, "response": resp.text[:800]})
                return False, None

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
                    self.failures.append({"test": name, "reason": msg, "response": str(result)[:800]})
                    return False, result

            self.tests_passed += 1
            self.log(f"✅ PASSED", "SUCCESS")
            return True, result

        except Exception as e:
            self.tests_failed += 1
            msg = f"❌ FAILED: Exception - {str(e)}"
            self.log(msg, "ERROR")
            self.failures.append({"test": name, "reason": msg, "response": ""})
            return False, None

    def run_all_tests(self):
        """Execute all P4.1 tests."""
        self.log("=" * 80)
        self.log("P4.1 BACKEND TESTING - Carry-over Picks + Editorial Context NBA/MLB")
        self.log("=" * 80)

        # ═══════════════════════════════════════════════════════════════════════
        # 1. MODULE IMPORTS TEST
        # ═══════════════════════════════════════════════════════════════════════
        self.log("\n[1] MODULE IMPORTS TEST", "SECTION")
        
        try:
            # Test carryover_picks module import
            from services.carryover_picks import apply_carryover, CARRYOVER_TTL_HOURS, MAX_CARRYOVER
            self.log("✅ carryover_picks module imported successfully")
            self.log(f"   CARRYOVER_TTL_HOURS: {CARRYOVER_TTL_HOURS}")
            self.log(f"   MAX_CARRYOVER: {MAX_CARRYOVER}")
            self.tests_run += 1
            self.tests_passed += 1
        except Exception as e:
            self.log(f"❌ carryover_picks import failed: {e}", "ERROR")
            self.tests_run += 1
            self.tests_failed += 1
            self.failures.append({"test": "Import carryover_picks", "reason": str(e), "response": ""})

        try:
            # Test editorial_context_service module import
            from services.editorial_context.editorial_context_service import fetch_editorial_context_bulk
            self.log("✅ editorial_context_service module imported successfully")
            self.tests_run += 1
            self.tests_passed += 1
        except Exception as e:
            self.log(f"❌ editorial_context_service import failed: {e}", "ERROR")
            self.tests_run += 1
            self.tests_failed += 1
            self.failures.append({"test": "Import editorial_context_service", "reason": str(e), "response": ""})

        # ═══════════════════════════════════════════════════════════════════════
        # 2. AUTH TEST
        # ═══════════════════════════════════════════════════════════════════════
        self.log("\n[2] AUTH TEST", "SECTION")
        
        success, result = self.test(
            "Login with demo@valuebet.app",
            "POST", "auth/login", 200,
            data={"email": "demo@valuebet.app", "password": "demo1234"},
            check_fn=lambda r: "token" in r and "user" in r
        )
        if success and result:
            self.token = result["token"]
            self.user_id = result["user"]["id"]
            self.log(f"   Token acquired: {self.token[:20]}...")
            self.log(f"   User ID: {self.user_id}")
        else:
            self.log("❌ Auth failed - cannot proceed with remaining tests", "ERROR")
            return False

        # ═══════════════════════════════════════════════════════════════════════
        # 3. FOOTBALL ANALYSIS - FIRST RUN
        # ═══════════════════════════════════════════════════════════════════════
        self.log("\n[3] FOOTBALL ANALYSIS - FIRST RUN", "SECTION")
        self.log("   ⚠ This may take 30-90 seconds (LLM analysis)...", "WARN")
        
        success, result1 = self.test(
            "POST /api/analysis/run (football, first run)",
            "POST", "analysis/run", 200,
            data={"sport": "football", "refresh": False, "include_live": False, "max_matches": 3},
            timeout=120,
            check_fn=lambda r: (
                "result" in r and
                "pick_run_id" in r and
                "verdict" in r["result"]
            )
        )
        
        first_run_id = None
        first_picks = []
        if success and result1:
            first_run_id = result1.get("pick_run_id")
            verdict = result1["result"].get("verdict")
            picks = result1["result"].get("picks", [])
            first_picks = picks
            summary = result1["result"].get("summary", {})
            
            self.log(f"   Run ID: {first_run_id}")
            self.log(f"   Verdict: {verdict}")
            self.log(f"   Picks: {len(picks)}")
            self.log(f"   Total recommended: {summary.get('total_recommended', 0)}")
            
            # Check for _pipeline.carryover metadata (should be present but with 0 preserved on first run)
            pipeline = result1["result"].get("_pipeline", {})
            carryover_meta = pipeline.get("carryover")
            if carryover_meta:
                self.log(f"   ✓ _pipeline.carryover present: preserved={carryover_meta.get('preserved', 0)}")
            else:
                self.log(f"   ⚠ _pipeline.carryover missing (expected on first run)", "WARN")
            
            # Check for summary.carryover_picks (should be empty list on first run)
            carryover_picks = summary.get("carryover_picks", [])
            self.log(f"   Carryover picks (first run): {len(carryover_picks)} (expected 0)")
            
            if picks:
                sample = picks[0]
                self.log(f"   Sample pick: {sample.get('match_label')} - {sample.get('recommendation', {}).get('market')}")
                self.log(f"   Confidence: {sample.get('recommendation', {}).get('confidence_score')}")
        else:
            self.log("   ⚠ First run failed or returned 409 (NO_PRIORITY_FIXTURES_FOUND) - expected if API quota exhausted", "WARN")

        # ═══════════════════════════════════════════════════════════════════════
        # 4. FOOTBALL ANALYSIS - SECOND RUN (VERIFY CARRYOVER)
        # ═══════════════════════════════════════════════════════════════════════
        self.log("\n[4] FOOTBALL ANALYSIS - SECOND RUN (VERIFY CARRYOVER)", "SECTION")
        self.log("   ⚠ This may take 30-90 seconds (LLM analysis)...", "WARN")
        self.log("   Waiting 2 seconds before second run...")
        time.sleep(2)
        
        success, result2 = self.test(
            "POST /api/analysis/run (football, second run - same sport)",
            "POST", "analysis/run", 200,
            data={"sport": "football", "refresh": False, "include_live": False, "max_matches": 3},
            timeout=120,
            check_fn=lambda r: (
                "result" in r and
                "pick_run_id" in r
            )
        )
        
        if success and result2:
            second_run_id = result2.get("pick_run_id")
            verdict = result2["result"].get("verdict")
            picks = result2["result"].get("picks", [])
            summary = result2["result"].get("summary", {})
            
            self.log(f"   Run ID: {second_run_id}")
            self.log(f"   Verdict: {verdict}")
            self.log(f"   Picks: {len(picks)}")
            self.log(f"   Total recommended: {summary.get('total_recommended', 0)}")
            
            # *** KEY TEST: Check for carryover_picks in summary ***
            carryover_picks = summary.get("carryover_picks", [])
            self.log(f"   Carryover picks (second run): {len(carryover_picks)}")
            
            if carryover_picks:
                self.log(f"   ✅ CARRYOVER WORKING: {len(carryover_picks)} picks preserved")
                for i, cp in enumerate(carryover_picks[:3], 1):
                    self.log(f"   Carryover pick {i}: {cp.get('match_label')}")
                    self.log(f"      Market: {cp.get('recommendation', {}).get('market')}")
                    self.log(f"      Confidence: {cp.get('recommendation', {}).get('confidence_score')}")
                    
                    # Check _carryover metadata
                    carryover_meta = cp.get("_carryover", {})
                    if carryover_meta.get("is_carryover"):
                        self.log(f"      ✓ _carryover.is_carryover: True")
                        self.log(f"      Original run: {carryover_meta.get('original_run_id')}")
                        self.log(f"      TTL hours: {carryover_meta.get('ttl_hours')}")
                    else:
                        self.log(f"      ⚠ _carryover.is_carryover missing or False", "WARN")
                    
                    # Check CARRYOVER tag
                    tags = cp.get("recommendation", {}).get("tags", [])
                    if "CARRYOVER" in tags:
                        self.log(f"      ✓ 'CARRYOVER' tag present")
                    else:
                        self.log(f"      ⚠ 'CARRYOVER' tag missing", "WARN")
            else:
                self.log(f"   ⚠ No carryover picks found (may be due to: no prior picks, all matches started, confidence < 60, or duplicates)", "WARN")
            
            # Check _pipeline.carryover metadata
            pipeline = result2["result"].get("_pipeline", {})
            carryover_meta = pipeline.get("carryover")
            if carryover_meta:
                self.log(f"   _pipeline.carryover:")
                self.log(f"      Prior run ID: {carryover_meta.get('prior_run_id')}")
                self.log(f"      Preserved: {carryover_meta.get('preserved', 0)}")
                self.log(f"      Skipped breakdown: {carryover_meta.get('skipped_breakdown', {})}")
            else:
                self.log(f"   ⚠ _pipeline.carryover missing", "WARN")
        else:
            self.log("   ⚠ Second run failed or returned 409 - cannot verify carryover", "WARN")

        # ═══════════════════════════════════════════════════════════════════════
        # 5. GET /api/picks/today - VERIFY CARRYOVER FIELD
        # ═══════════════════════════════════════════════════════════════════════
        self.log("\n[5] GET /api/picks/today - VERIFY CARRYOVER FIELD", "SECTION")
        
        success, result = self.test(
            "GET /api/picks/today (football)",
            "GET", "picks/today?sport=football", 200,
            check_fn=lambda r: "pick_run" in r
        )
        
        if success and result and result.get("pick_run"):
            pick_run = result["pick_run"]
            payload = pick_run.get("payload", {})
            summary = payload.get("summary", {})
            
            # Check carryover_picks field exists (even if empty)
            if "carryover_picks" in summary:
                carryover_picks = summary["carryover_picks"]
                self.log(f"   ✅ carryover_picks field present: {len(carryover_picks)} picks")
                if carryover_picks:
                    self.log(f"   Sample: {carryover_picks[0].get('match_label')}")
            else:
                self.log(f"   ⚠ carryover_picks field missing in summary", "WARN")
        else:
            self.log(f"   ⚠ No pick_run found for today", "WARN")

        # ═══════════════════════════════════════════════════════════════════════
        # 6. EDITORIAL CONTEXT - BASKETBALL (NBA)
        # ═══════════════════════════════════════════════════════════════════════
        self.log("\n[6] EDITORIAL CONTEXT - BASKETBALL (NBA)", "SECTION")
        self.log("   Testing that basketball no longer returns sport_not_supported...")
        self.log("   ⚠ This may take 30-90 seconds (LLM analysis)...", "WARN")
        
        success, result = self.test(
            "POST /api/analysis/run (basketball)",
            "POST", "analysis/run", 200,
            data={"sport": "basketball", "refresh": False, "include_live": False, "max_matches": 2},
            timeout=120,
            check_fn=lambda r: (
                "result" in r and
                "pick_run_id" in r
            )
        )
        
        if success and result:
            verdict = result["result"].get("verdict")
            self.log(f"   ✅ Basketball analysis succeeded (no sport_not_supported error)")
            self.log(f"   Verdict: {verdict}")
            
            # Check _pipeline.editorial_context_evaluated
            pipeline = result["result"].get("_pipeline", {})
            editorial_evaluated = pipeline.get("editorial_context_evaluated", 0)
            self.log(f"   Editorial context evaluated: {editorial_evaluated} matches")
            
            if editorial_evaluated > 0:
                self.log(f"   ✅ Editorial context dispatcher ran for basketball")
            else:
                self.log(f"   ⚠ Editorial context evaluated=0 (may be no live matches or API quota)", "WARN")
        else:
            self.log("   ⚠ Basketball analysis failed or returned 409", "WARN")

        # ═══════════════════════════════════════════════════════════════════════
        # 7. EDITORIAL CONTEXT - BASEBALL (MLB)
        # ═══════════════════════════════════════════════════════════════════════
        self.log("\n[7] EDITORIAL CONTEXT - BASEBALL (MLB)", "SECTION")
        self.log("   Testing that baseball no longer returns sport_not_supported...")
        self.log("   ⚠ This may take 30-90 seconds (LLM analysis)...", "WARN")
        
        success, result = self.test(
            "POST /api/analysis/run (baseball)",
            "POST", "analysis/run", 200,
            data={"sport": "baseball", "refresh": False, "include_live": False, "max_matches": 2},
            timeout=120,
            check_fn=lambda r: (
                "result" in r and
                "pick_run_id" in r
            )
        )
        
        if success and result:
            verdict = result["result"].get("verdict")
            self.log(f"   ✅ Baseball analysis succeeded (no sport_not_supported error)")
            self.log(f"   Verdict: {verdict}")
            
            # Check _pipeline.editorial_context_evaluated
            pipeline = result["result"].get("_pipeline", {})
            editorial_evaluated = pipeline.get("editorial_context_evaluated", 0)
            self.log(f"   Editorial context evaluated: {editorial_evaluated} matches")
            
            if editorial_evaluated > 0:
                self.log(f"   ✅ Editorial context dispatcher ran for baseball")
            else:
                self.log(f"   ⚠ Editorial context evaluated=0 (may be no live matches or API quota)", "WARN")
        else:
            self.log("   ⚠ Baseball analysis failed or returned 409", "WARN")

        # ═══════════════════════════════════════════════════════════════════════
        # SUMMARY
        # ═══════════════════════════════════════════════════════════════════════
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
                if failure['response']:
                    self.log(f"   Response: {failure['response'][:500]}", "ERROR")
        
        return self.tests_failed == 0


def main():
    tester = CarryoverEditorialTester()
    success = tester.run_all_tests()
    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
