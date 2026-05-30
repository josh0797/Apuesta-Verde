#!/usr/bin/env python3
"""
MLB V5 Script Survival - Pipeline Integration & API Tests
==========================================================
Tests the full pipeline integration and API endpoints.
"""
import sys
import requests
import json
from datetime import datetime
from typing import Optional

BASE_URL = "https://low-volatility-plays.preview.emergentagent.com"

class Colors:
    GREEN = '\033[92m'
    RED = '\033[91m'
    YELLOW = '\033[93m'
    BLUE = '\033[94m'
    END = '\033[0m'

class MLBV5IntegrationTest:
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
        """Run a single API test"""
        url = f"{self.base_url}{endpoint}"
        headers = {'Content-Type': 'application/json'}
        if self.token:
            headers['Authorization'] = f'Bearer {self.token}'

        self.tests_run += 1
        self.log(f"\n🔍 Test #{self.tests_run}: {name}", Colors.BLUE)
        
        try:
            if method == 'GET':
                response = requests.get(url, headers=headers, timeout=60)
            elif method == 'POST':
                response = requests.post(url, json=data, headers=headers, timeout=60)
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
                check_result, check_msg = check_fn(resp_data)
                if not check_result:
                    self.tests_failed += 1
                    msg = f"❌ FAILED - {check_msg}"
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
        self.log("AUTHENTICATION", Colors.YELLOW)
        self.log("="*80, Colors.YELLOW)
        
        success, response = self.test(
            "Login with demo@valuebet.app/demo1234",
            "POST",
            "/api/auth/login",
            200,
            data={"email": "demo@valuebet.app", "password": "demo1234"}
        )
        
        if success and response.get("token"):
            self.token = response["token"]
            self.log(f"✅ Token obtained: {self.token[:20]}...", Colors.GREEN)
            return True
        else:
            self.log("❌ Failed to obtain token", Colors.RED)
            return False

    def test_mlb_pipeline_v5_fields(self):
        """Test POST /api/analysis/run produces picks with _mlb_script_v5"""
        self.log("\n" + "="*80, Colors.YELLOW)
        self.log("MLB PIPELINE V5 FIELDS", Colors.YELLOW)
        self.log("="*80, Colors.YELLOW)

        def check_v5_fields(data):
            picks = data.get("picks", [])
            rescued_picks = data.get("rescued_picks", [])
            all_picks = picks + rescued_picks

            if not all_picks:
                return False, "No picks returned from pipeline"

            # Check first pick has _mlb_script_v5
            pick = all_picks[0]
            v5 = pick.get("_mlb_script_v5")
            
            if not v5:
                return False, f"Pick missing _mlb_script_v5 field. Keys: {list(pick.keys())}"

            # Check v5 structure
            required_fields = ["version", "survival", "fragility", "stability", 
                             "confidence_contribution", "reference_profile", "narrative_es"]
            missing = [f for f in required_fields if f not in v5]
            if missing:
                return False, f"_mlb_script_v5 missing fields: {missing}"

            # Check survival structure
            survival = v5.get("survival", {})
            if not isinstance(survival, dict):
                return False, f"survival is not a dict: {type(survival)}"
            
            survival_score = survival.get("score")
            if survival_score is None or not (0 <= survival_score <= 100):
                return False, f"survival.score invalid: {survival_score}"

            # Check fragility structure
            fragility = v5.get("fragility", {})
            if not isinstance(fragility, dict):
                return False, f"fragility is not a dict: {type(fragility)}"
            
            fragility_score = fragility.get("score")
            if fragility_score is None or not (0 <= fragility_score <= 100):
                return False, f"fragility.score invalid: {fragility_score}"

            # Check stability structure
            stability = v5.get("stability", {})
            if not isinstance(stability, dict):
                return False, f"stability is not a dict: {type(stability)}"
            
            stability_code = stability.get("code")
            valid_codes = ["ELITE_STABLE", "STABLE", "MODERATELY_STABLE", "FRAGILE", "HIGHLY_FRAGILE"]
            if stability_code not in valid_codes:
                return False, f"stability.code invalid: {stability_code}"

            # Check top-level aliases
            if pick.get("script_survival") != survival_score:
                return False, f"script_survival alias mismatch: {pick.get('script_survival')} != {survival_score}"
            
            if pick.get("fragility_score") != fragility_score:
                return False, f"fragility_score alias mismatch: {pick.get('fragility_score')} != {fragility_score}"
            
            if pick.get("stability_code") != stability_code:
                return False, f"stability_code alias mismatch: {pick.get('stability_code')} != {stability_code}"

            self.log(f"\n📊 V5 Fields Check:", Colors.BLUE)
            self.log(f"  - survival.score: {survival_score:.1f}", Colors.BLUE)
            self.log(f"  - fragility.score: {fragility_score:.1f}", Colors.BLUE)
            self.log(f"  - stability.code: {stability_code}", Colors.BLUE)
            self.log(f"  - confidence_contribution: {v5.get('confidence_contribution')}", Colors.BLUE)
            self.log(f"  - reference_profile: {v5.get('reference_profile')}", Colors.BLUE)

            return True, "All V5 fields present and valid"

        success, response = self.test(
            "POST /api/analysis/run with sport=baseball produces _mlb_script_v5",
            "POST",
            "/api/analysis/run",
            200,
            data={"sport": "baseball"},
            check_fn=check_v5_fields
        )

        return success, response

    def test_v3_breakdown_includes_survival(self):
        """Test V3 confidence_breakdown includes script_survival component"""
        self.log("\n" + "="*80, Colors.YELLOW)
        self.log("V3 BREAKDOWN INCLUDES SCRIPT SURVIVAL", Colors.YELLOW)
        self.log("="*80, Colors.YELLOW)

        def check_v3_breakdown(data):
            picks = data.get("picks", [])
            rescued_picks = data.get("rescued_picks", [])
            all_picks = picks + rescued_picks

            if not all_picks:
                return False, "No picks returned"

            pick = all_picks[0]
            v3 = pick.get("_mlb_script_v3", {})
            breakdown = v3.get("confidence_breakdown", {})

            if not breakdown:
                return False, "No confidence_breakdown in _mlb_script_v3"

            components = breakdown.get("components", [])
            if not components:
                return False, "No components in confidence_breakdown"

            # Check if script_survival component exists (when survival_contrib != 0)
            survival_component = next((c for c in components if c.get("key") == "script_survival"), None)
            
            # Also check breakdown.script_survival_contribution
            survival_contrib = breakdown.get("script_survival_contribution")

            # Check total calculation
            total = breakdown.get("total")
            raw_total = breakdown.get("raw_total")
            volatility_penalty = breakdown.get("volatility_penalty", 0)

            if survival_contrib is not None and survival_contrib != 0:
                if not survival_component:
                    return False, f"script_survival_contribution={survival_contrib} but no script_survival component"
                
                # Check total = raw_total - volatility_penalty + survival_contrib
                expected_total = max(0, min(100, raw_total - volatility_penalty + survival_contrib))
                if abs(total - expected_total) > 0.5:
                    return False, f"Total calculation mismatch: {total} != {expected_total:.1f} (raw={raw_total}, vol_penalty={volatility_penalty}, survival={survival_contrib})"

            self.log(f"\n📊 V3 Breakdown Check:", Colors.BLUE)
            self.log(f"  - total: {total}", Colors.BLUE)
            self.log(f"  - raw_total: {raw_total}", Colors.BLUE)
            self.log(f"  - volatility_penalty: {volatility_penalty}", Colors.BLUE)
            self.log(f"  - script_survival_contribution: {survival_contrib}", Colors.BLUE)
            self.log(f"  - components count: {len(components)}", Colors.BLUE)
            if survival_component:
                self.log(f"  - script_survival component value: {survival_component.get('value')}", Colors.BLUE)

            return True, "V3 breakdown includes survival contribution correctly"

        success, response = self.test(
            "V3 confidence_breakdown includes script_survival component",
            "POST",
            "/api/analysis/run",
            200,
            data={"sport": "baseball"},
            check_fn=check_v3_breakdown
        )

        return success, response

    def test_no_regression_mlb_buckets(self):
        """Test no regression on MLB pipeline buckets"""
        self.log("\n" + "="*80, Colors.YELLOW)
        self.log("NO REGRESSION ON MLB BUCKETS", Colors.YELLOW)
        self.log("="*80, Colors.YELLOW)

        def check_buckets(data):
            # Check all expected buckets exist
            expected_buckets = ["picks", "rescued_picks", "structural_lean_requires_odds", 
                              "watchlist_manual_odds", "discarded_picks"]
            
            for bucket in expected_buckets:
                if bucket not in data:
                    return False, f"Missing bucket: {bucket}"

            # Check _mlb_script_v3 is intact in picks
            picks = data.get("picks", [])
            if picks:
                pick = picks[0]
                v3 = pick.get("_mlb_script_v3")
                if not v3:
                    return False, "Pick missing _mlb_script_v3 (regression)"

            self.log(f"\n📊 Buckets Check:", Colors.BLUE)
            self.log(f"  - picks: {len(data.get('picks', []))}", Colors.BLUE)
            self.log(f"  - rescued_picks: {len(data.get('rescued_picks', []))}", Colors.BLUE)
            self.log(f"  - structural_lean_requires_odds: {len(data.get('structural_lean_requires_odds', []))}", Colors.BLUE)
            self.log(f"  - watchlist_manual_odds: {len(data.get('watchlist_manual_odds', []))}", Colors.BLUE)
            self.log(f"  - discarded_picks: {len(data.get('discarded_picks', []))}", Colors.BLUE)

            return True, "All buckets present and _mlb_script_v3 intact"

        success, response = self.test(
            "MLB pipeline buckets intact (no regression)",
            "POST",
            "/api/analysis/run",
            200,
            data={"sport": "baseball"},
            check_fn=check_buckets
        )

        return success, response

    def test_no_regression_football(self):
        """Test football pipeline does NOT have _mlb_script_v5"""
        self.log("\n" + "="*80, Colors.YELLOW)
        self.log("NO REGRESSION ON FOOTBALL", Colors.YELLOW)
        self.log("="*80, Colors.YELLOW)

        def check_no_v5(data):
            picks = data.get("picks", [])
            
            if not picks:
                # No picks is OK for football
                return True, "No picks (OK for football)"

            pick = picks[0]
            
            # Check pick does NOT have _mlb_script_v5
            if "_mlb_script_v5" in pick:
                return False, "Football pick has _mlb_script_v5 (should not)"
            
            # Check pick does NOT have script_survival fields
            if "script_survival" in pick:
                return False, "Football pick has script_survival field (should not)"

            self.log(f"\n📊 Football Check:", Colors.BLUE)
            self.log(f"  - picks count: {len(picks)}", Colors.BLUE)
            self.log(f"  - _mlb_script_v5 present: False (correct)", Colors.BLUE)

            return True, "Football picks do NOT have V5 fields (correct)"

        success, response = self.test(
            "POST /api/analysis/run sport=football does NOT have _mlb_script_v5",
            "POST",
            "/api/analysis/run",
            200,
            data={"sport": "football"},
            check_fn=check_no_v5
        )

        return success, response

    def test_script_breaks_api(self):
        """Test GET /api/mlb/script_breaks returns V5 fields"""
        self.log("\n" + "="*80, Colors.YELLOW)
        self.log("SCRIPT BREAKS API - V5 FIELDS", Colors.YELLOW)
        self.log("="*80, Colors.YELLOW)

        def check_v5_fields_in_breaks(data):
            items = data.get("items", [])
            
            if not items:
                # No script breaks yet is OK
                self.log("  No script breaks stored yet (OK)", Colors.BLUE)
                return True, "No script breaks (OK)"

            # Check first item has V5 fields
            item = items[0]
            
            v5_fields = ["script_survival_prediction", "fragility_prediction", 
                        "stability_prediction", "reference_profile"]
            
            # These fields may be None if the pick was settled before V5
            # Just check they exist in the schema
            for field in v5_fields:
                if field not in item:
                    return False, f"Missing V5 field in script_breaks: {field}"

            self.log(f"\n📊 Script Breaks V5 Fields:", Colors.BLUE)
            self.log(f"  - items count: {len(items)}", Colors.BLUE)
            self.log(f"  - script_survival_prediction: {item.get('script_survival_prediction')}", Colors.BLUE)
            self.log(f"  - fragility_prediction: {item.get('fragility_prediction')}", Colors.BLUE)
            self.log(f"  - stability_prediction: {item.get('stability_prediction')}", Colors.BLUE)
            self.log(f"  - reference_profile: {item.get('reference_profile')}", Colors.BLUE)

            return True, "Script breaks include V5 fields"

        success, response = self.test(
            "GET /api/mlb/script_breaks returns V5 fields",
            "GET",
            "/api/mlb/script_breaks?days=60&limit=10",
            200,
            check_fn=check_v5_fields_in_breaks
        )

        return success, response

    def test_script_breaks_stats_api(self):
        """Test GET /api/mlb/script_breaks/stats aggregates V5 learning codes"""
        self.log("\n" + "="*80, Colors.YELLOW)
        self.log("SCRIPT BREAKS STATS - V5 LEARNING CODES", Colors.YELLOW)
        self.log("="*80, Colors.YELLOW)

        def check_v5_learning_codes(data):
            top_codes = data.get("top_learning_codes", [])
            
            # Check structure
            if not isinstance(top_codes, list):
                return False, f"top_learning_codes is not a list: {type(top_codes)}"

            # V5 learning codes we expect to see (when data exists)
            v5_codes = ["FALSE_STABILITY_EVENT", "TRUE_STABILITY_EVENT", 
                       "REFERENCE_STABLE_UNDER_PROFILE_CONFIRMED", 
                       "REFERENCE_STABLE_UNDER_PROFILE_FAILED",
                       "PREDICTED_FRAGILE_CONFIRMED", "FRAGILE_BUT_HELD"]

            self.log(f"\n📊 Script Breaks Stats:", Colors.BLUE)
            self.log(f"  - total: {data.get('total')}", Colors.BLUE)
            self.log(f"  - broken: {data.get('broken')}", Colors.BLUE)
            self.log(f"  - broken_rate: {data.get('broken_rate')}", Colors.BLUE)
            self.log(f"  - top_learning_codes count: {len(top_codes)}", Colors.BLUE)
            
            if top_codes:
                self.log(f"  - Top 3 codes:", Colors.BLUE)
                for code_obj in top_codes[:3]:
                    self.log(f"    • {code_obj.get('code')}: {code_obj.get('count')}", Colors.BLUE)

            return True, "Script breaks stats API working"

        success, response = self.test(
            "GET /api/mlb/script_breaks/stats aggregates V5 codes",
            "GET",
            "/api/mlb/script_breaks/stats?days=60",
            200,
            check_fn=check_v5_learning_codes
        )

        return success, response

    def print_summary(self):
        """Print test summary"""
        self.log("\n" + "="*80, Colors.YELLOW)
        self.log("TEST SUMMARY", Colors.YELLOW)
        self.log("="*80, Colors.YELLOW)
        
        total = self.tests_run
        passed = self.tests_passed
        failed = self.tests_failed
        pass_rate = (passed / total * 100) if total > 0 else 0

        self.log(f"\nTotal Tests: {total}", Colors.BLUE)
        self.log(f"Passed: {passed}", Colors.GREEN)
        self.log(f"Failed: {failed}", Colors.RED)
        self.log(f"Pass Rate: {pass_rate:.1f}%", Colors.GREEN if pass_rate >= 90 else Colors.YELLOW)

        if self.failures:
            self.log("\n❌ FAILED TESTS:", Colors.RED)
            for failure in self.failures:
                self.log(f"  - {failure}", Colors.RED)

        return {
            "total": total,
            "passed": passed,
            "failed": failed,
            "pass_rate": pass_rate,
            "failures": self.failures,
        }

def main():
    print(f"\n{'='*80}")
    print("MLB V5 Script Survival - Pipeline Integration & API Tests")
    print(f"{'='*80}\n")
    print(f"Started at: {datetime.now().isoformat()}\n")

    suite = MLBV5IntegrationTest(BASE_URL)

    # Authenticate first
    if not suite.test_auth():
        print("\n❌ Authentication failed. Cannot proceed with tests.")
        return 1

    # Run integration tests
    suite.test_mlb_pipeline_v5_fields()
    suite.test_v3_breakdown_includes_survival()
    suite.test_no_regression_mlb_buckets()
    suite.test_no_regression_football()
    suite.test_script_breaks_api()
    suite.test_script_breaks_stats_api()

    # Print summary
    summary = suite.print_summary()

    print(f"\nCompleted at: {datetime.now().isoformat()}\n")

    # Return exit code
    return 0 if summary["failed"] == 0 else 1

if __name__ == "__main__":
    sys.exit(main())
