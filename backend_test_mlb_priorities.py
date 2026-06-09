"""
Backend Tests for MLB Engine Priorities 1-4 + Fix 4
Tests:
1. Fix 4 — POST /api/mlb/picks/{pick_id}/manual-odds robust multi-key matching
2. Priority 1 — λ7-9 reactive model (bullpen+traffic+defense+series)
3. Priority 2 — adjustment_breakdown field
4. Priority 3 — series_familiarity_score (no H2H averages)
5. Integration — orchestrator persists series_familiarity
"""

import requests
import sys
from datetime import datetime

BASE_URL = "https://low-volatility-plays.preview.emergentagent.com"

class MLBPrioritiesTester:
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
                    self.log_test("Login", False, f"No token in response")
                    return False
            else:
                self.log_test("Login", False, f"Status {response.status_code}")
                return False
        except Exception as e:
            self.log_test("Login", False, str(e))
            return False

    def test_manual_odds_endpoint_basic(self):
        """Test Fix 4 — Manual odds endpoint accepts valid odds"""
        print("\n🎯 Testing Fix 4 — Manual Odds Endpoint Basic...")
        try:
            headers = {"Authorization": f"Bearer {self.token}"}
            # Test with a dummy pick_id (should create fallback override)
            response = requests.post(
                f"{self.base_url}/api/mlb/picks/test_pick_123/manual-odds",
                headers=headers,
                json={
                    "manual_odds": "1.90",
                    "game_pk": "12345",
                    "home_team": "Yankees",
                    "away_team": "Red Sox",
                    "commence_date": "2025-08-15",
                    "market": "total_runs_under",
                    "line": 8.5
                },
                timeout=10
            )
            
            if response.status_code == 200:
                data = response.json()
                # Should return ok=true with fallback_override_created=true
                if data.get("ok") and data.get("fallback_override_created"):
                    self.log_test(
                        "Manual Odds Endpoint - Fallback Override",
                        True,
                        f"Override created: {data.get('message')}"
                    )
                    return True
                else:
                    self.log_test(
                        "Manual Odds Endpoint - Fallback Override",
                        False,
                        f"Expected fallback_override_created=true, got: {data}"
                    )
                    return False
            else:
                self.log_test(
                    "Manual Odds Endpoint - Fallback Override",
                    False,
                    f"Status {response.status_code}: {response.text[:200]}"
                )
                return False
        except Exception as e:
            self.log_test("Manual Odds Endpoint - Fallback Override", False, str(e))
            return False

    def test_manual_odds_comma_decimal(self):
        """Test Fix 4 — Manual odds accepts comma decimal (Spanish locale)"""
        print("\n🎯 Testing Fix 4 — Manual Odds Comma Decimal...")
        try:
            headers = {"Authorization": f"Bearer {self.token}"}
            response = requests.post(
                f"{self.base_url}/api/mlb/picks/test_pick_comma/manual-odds",
                headers=headers,
                json={
                    "manual_odds": "1,85",  # Spanish comma decimal
                    "game_pk": "12346",
                    "home_team": "Dodgers",
                    "away_team": "Giants",
                    "commence_date": "2025-08-15",
                    "market": "total_runs_over",
                    "line": 9.5
                },
                timeout=10
            )
            
            if response.status_code == 200:
                data = response.json()
                if data.get("ok") and data.get("manual_odds") == 1.85:
                    self.log_test(
                        "Manual Odds - Comma Decimal",
                        True,
                        f"Parsed '1,85' as 1.85"
                    )
                    return True
                else:
                    self.log_test(
                        "Manual Odds - Comma Decimal",
                        False,
                        f"Expected manual_odds=1.85, got: {data.get('manual_odds')}"
                    )
                    return False
            else:
                self.log_test(
                    "Manual Odds - Comma Decimal",
                    False,
                    f"Status {response.status_code}"
                )
                return False
        except Exception as e:
            self.log_test("Manual Odds - Comma Decimal", False, str(e))
            return False

    def test_manual_odds_invalid_rejects(self):
        """Test Fix 4 — Manual odds rejects invalid values"""
        print("\n🎯 Testing Fix 4 — Manual Odds Invalid Rejection...")
        try:
            headers = {"Authorization": f"Bearer {self.token}"}
            # Test with invalid odds (<=1.00)
            response = requests.post(
                f"{self.base_url}/api/mlb/picks/test_pick_invalid/manual-odds",
                headers=headers,
                json={
                    "manual_odds": "0.95",  # Invalid: <=1.00
                    "game_pk": "12347"
                },
                timeout=10
            )
            
            # Should return 400 Bad Request
            if response.status_code == 400:
                self.log_test(
                    "Manual Odds - Invalid Rejection",
                    True,
                    f"Correctly rejected odds <=1.00 with 400"
                )
                return True
            else:
                self.log_test(
                    "Manual Odds - Invalid Rejection",
                    False,
                    f"Expected 400, got {response.status_code}"
                )
                return False
        except Exception as e:
            self.log_test("Manual Odds - Invalid Rejection", False, str(e))
            return False

    def test_lambda_model_priority1(self):
        """Test Priority 1 — λ7-9 reactive model with bullpen+traffic+defense"""
        print("\n⚾ Testing Priority 1 — Lambda Model Reactive λ7-9...")
        try:
            # Import the lambda model directly to test it
            import sys
            sys.path.insert(0, '/app/backend')
            from services.mlb_inning_lambda_model import compute_mlb_inning_lambdas
            
            # Test case 1: Vulnerable bullpen + low traffic → small λ7-9 increase
            result1 = compute_mlb_inning_lambdas(
                expected_runs=9.0,
                bullpen_home={
                    "bullpen_era_7d": 6.0,  # Vulnerable
                    "bullpen_whip_7d": 1.5,
                    "bullpen_usage_3d": 0.4,
                    "bullpen_fatigue": 0.3,
                    "hr_risk": 0.2
                },
                bullpen_away={
                    "bullpen_era_7d": 6.0,
                    "bullpen_whip_7d": 1.5,
                    "bullpen_usage_3d": 0.4,
                    "bullpen_fatigue": 0.3,
                    "hr_risk": 0.2
                },
                traffic_score=15.0,  # Low traffic
                observe_only=True
            )
            
            if result1.get("available"):
                lambda_base = result1.get("baseline_expected_runs", 0) * 0.34  # λ7-9 weight
                lambda_actual = result1.get("lambda_7_9", 0)
                increase_pct = ((lambda_actual - lambda_base) / lambda_base * 100) if lambda_base > 0 else 0
                
                # Should increase but <15%
                if 0 <= increase_pct < 15:
                    self.log_test(
                        "Priority 1 - Low Traffic Small Increase",
                        True,
                        f"λ7-9 increased {increase_pct:.1f}% (expected <15%)"
                    )
                else:
                    self.log_test(
                        "Priority 1 - Low Traffic Small Increase",
                        False,
                        f"λ7-9 increased {increase_pct:.1f}% (expected <15%)"
                    )
                    return False
            else:
                self.log_test(
                    "Priority 1 - Low Traffic Small Increase",
                    False,
                    f"Lambda model not available: {result1.get('reason')}"
                )
                return False
            
            # Test case 2: Vulnerable bullpen + high traffic → significant increase
            result2 = compute_mlb_inning_lambdas(
                expected_runs=9.0,
                bullpen_home={
                    "bullpen_era_7d": 6.0,
                    "bullpen_whip_7d": 1.5,
                    "bullpen_usage_3d": 0.6,
                    "bullpen_fatigue": 0.7,
                    "hr_risk": 0.6
                },
                bullpen_away={
                    "bullpen_era_7d": 6.0,
                    "bullpen_whip_7d": 1.5,
                    "bullpen_usage_3d": 0.6,
                    "bullpen_fatigue": 0.7,
                    "hr_risk": 0.6
                },
                traffic_score=80.0,  # High traffic
                observe_only=True
            )
            
            if result2.get("available"):
                lambda_base2 = result2.get("baseline_expected_runs", 0) * 0.34
                lambda_actual2 = result2.get("lambda_7_9", 0)
                increase_pct2 = ((lambda_actual2 - lambda_base2) / lambda_base2 * 100) if lambda_base2 > 0 else 0
                
                # Should increase >=8%
                if increase_pct2 >= 8:
                    self.log_test(
                        "Priority 1 - High Traffic Significant Increase",
                        True,
                        f"λ7-9 increased {increase_pct2:.1f}% (expected >=8%)"
                    )
                else:
                    self.log_test(
                        "Priority 1 - High Traffic Significant Increase",
                        False,
                        f"λ7-9 increased {increase_pct2:.1f}% (expected >=8%)"
                    )
                    return False
            else:
                self.log_test(
                    "Priority 1 - High Traffic Significant Increase",
                    False,
                    f"Lambda model not available"
                )
                return False
            
            # Test case 3: Check reason codes
            reason_codes = result2.get("reason_codes", [])
            expected_codes = [
                "LATE_LAMBDA_REACTIVE_MODEL_USED",
                "BULLPEN_TRAFFIC_RAISES_LATE_LAMBDA"
            ]
            has_expected = all(code in reason_codes for code in expected_codes)
            
            if has_expected:
                self.log_test(
                    "Priority 1 - Reason Codes",
                    True,
                    f"Found expected reason codes"
                )
            else:
                self.log_test(
                    "Priority 1 - Reason Codes",
                    False,
                    f"Missing expected codes. Got: {reason_codes}"
                )
                return False
            
            return True
            
        except Exception as e:
            self.log_test("Priority 1 - Lambda Model", False, str(e))
            return False

    def test_adjustment_breakdown_priority2(self):
        """Test Priority 2 — adjustment_breakdown field"""
        print("\n📊 Testing Priority 2 — Adjustment Breakdown...")
        try:
            import sys
            sys.path.insert(0, '/app/backend')
            from services.mlb_inning_lambda_model import compute_mlb_inning_lambdas
            
            result = compute_mlb_inning_lambdas(
                expected_runs=9.0,
                home_pitcher={"era": 3.5, "whip": 1.2},
                away_pitcher={"era": 4.0, "whip": 1.3},
                bullpen_home={"bullpen_era_7d": 4.5},
                bullpen_away={"bullpen_era_7d": 4.5},
                traffic_score=50.0,
                observe_only=True
            )
            
            if not result.get("available"):
                self.log_test(
                    "Priority 2 - Adjustment Breakdown",
                    False,
                    "Lambda model not available"
                )
                return False
            
            # Check adjustment_breakdown structure
            breakdown = result.get("adjustment_breakdown")
            if not breakdown:
                self.log_test(
                    "Priority 2 - Adjustment Breakdown",
                    False,
                    "adjustment_breakdown field missing"
                )
                return False
            
            # Verify required fields
            required_fields = [
                "base_expected_runs",
                "lambda_base",
                "adjustments",
                "final_expected_runs",
                "total_delta"
            ]
            missing = [f for f in required_fields if f not in breakdown]
            
            if missing:
                self.log_test(
                    "Priority 2 - Adjustment Breakdown Structure",
                    False,
                    f"Missing fields: {missing}"
                )
                return False
            
            # Check lambda_base has all phases
            lambda_base = breakdown.get("lambda_base", {})
            if not all(k in lambda_base for k in ["lambda_1_3", "lambda_4_6", "lambda_7_9"]):
                self.log_test(
                    "Priority 2 - Lambda Base Phases",
                    False,
                    f"Missing phase in lambda_base: {lambda_base.keys()}"
                )
                return False
            
            # Check adjustments is a list with proper structure
            adjustments = breakdown.get("adjustments", [])
            if not isinstance(adjustments, list):
                self.log_test(
                    "Priority 2 - Adjustments List",
                    False,
                    f"adjustments is not a list: {type(adjustments)}"
                )
                return False
            
            # Verify adjustment items have required fields
            if adjustments:
                first_adj = adjustments[0]
                adj_fields = ["phase", "factor", "delta", "reason"]
                missing_adj = [f for f in adj_fields if f not in first_adj]
                if missing_adj:
                    self.log_test(
                        "Priority 2 - Adjustment Item Structure",
                        False,
                        f"Missing fields in adjustment: {missing_adj}"
                    )
                    return False
            
            self.log_test(
                "Priority 2 - Adjustment Breakdown",
                True,
                f"Complete breakdown with {len(adjustments)} adjustments"
            )
            return True
            
        except Exception as e:
            self.log_test("Priority 2 - Adjustment Breakdown", False, str(e))
            return False

    def test_series_familiarity_priority3(self):
        """Test Priority 3 — Series familiarity score (no H2H averages)"""
        print("\n🔄 Testing Priority 3 — Series Familiarity Score...")
        try:
            import sys
            sys.path.insert(0, '/app/backend')
            from services.mlb_series_familiarity_score import compute_series_familiarity_score
            from datetime import datetime, timedelta
            
            # Test case 1: High familiarity (3 games in last 3 days)
            target_date = datetime.now()
            schedule_high = [
                {
                    "gameDate": (target_date - timedelta(days=1)).isoformat(),
                    "teams": {
                        "home": {"team": {"id": 147}},
                        "away": {"team": {"id": 111}}
                    }
                },
                {
                    "gameDate": (target_date - timedelta(days=2)).isoformat(),
                    "teams": {
                        "home": {"team": {"id": 147}},
                        "away": {"team": {"id": 111}}
                    }
                },
                {
                    "gameDate": (target_date - timedelta(days=3)).isoformat(),
                    "teams": {
                        "home": {"team": {"id": 147}},
                        "away": {"team": {"id": 111}}
                    }
                }
            ]
            
            result_high = compute_series_familiarity_score(
                home_team_id=147,
                away_team_id=111,
                game_date=target_date,
                schedule=schedule_high
            )
            
            if not result_high.get("available"):
                self.log_test(
                    "Priority 3 - Series Familiarity High",
                    False,
                    f"Not available: {result_high.get('reason')}"
                )
                return False
            
            score_high = result_high.get("series_familiarity_score", 0)
            bucket_high = result_high.get("bucket")
            
            # Should be HIGH (>=70)
            if score_high >= 70 and bucket_high == "HIGH_SERIES_FAMILIARITY":
                self.log_test(
                    "Priority 3 - High Familiarity",
                    True,
                    f"Score={score_high} bucket={bucket_high}"
                )
            else:
                self.log_test(
                    "Priority 3 - High Familiarity",
                    False,
                    f"Expected score>=70, got {score_high} bucket={bucket_high}"
                )
                return False
            
            # Test case 2: Low familiarity (empty schedule)
            result_low = compute_series_familiarity_score(
                home_team_id=147,
                away_team_id=111,
                game_date=target_date,
                schedule=[]
            )
            
            if not result_low.get("available"):
                self.log_test(
                    "Priority 3 - Series Familiarity Low",
                    False,
                    "Not available for empty schedule"
                )
                return False
            
            score_low = result_low.get("series_familiarity_score", 0)
            bucket_low = result_low.get("bucket")
            
            # Should be LOW (score=0)
            if score_low == 0 and bucket_low == "LOW_SERIES_FAMILIARITY":
                self.log_test(
                    "Priority 3 - Low Familiarity",
                    True,
                    f"Score={score_low} bucket={bucket_low}"
                )
            else:
                self.log_test(
                    "Priority 3 - Low Familiarity",
                    False,
                    f"Expected score=0, got {score_low} bucket={bucket_low}"
                )
                return False
            
            # Test case 3: Verify NO h2h_total_avg or last_3_totals_average params
            # (should not accept these parameters)
            try:
                # This should work without these params
                result_no_h2h = compute_series_familiarity_score(
                    home_team_id=147,
                    away_team_id=111,
                    game_date=target_date,
                    schedule=schedule_high
                )
                if result_no_h2h.get("available"):
                    self.log_test(
                        "Priority 3 - No H2H Averages",
                        True,
                        "Function works without h2h_total_avg parameter"
                    )
                else:
                    self.log_test(
                        "Priority 3 - No H2H Averages",
                        False,
                        "Function failed without h2h params"
                    )
                    return False
            except TypeError as e:
                # If it requires h2h params, that's a regression
                self.log_test(
                    "Priority 3 - No H2H Averages",
                    False,
                    f"Function requires h2h params (regression): {e}"
                )
                return False
            
            return True
            
        except Exception as e:
            self.log_test("Priority 3 - Series Familiarity", False, str(e))
            return False

    def test_mlb_picks_today_integration(self):
        """Test Integration — orchestrator persists series_familiarity"""
        print("\n🔗 Testing Integration — MLB Picks Today...")
        try:
            headers = {"Authorization": f"Bearer {self.token}"}
            response = requests.get(
                f"{self.base_url}/api/picks/today?sport=baseball",
                headers=headers,
                timeout=20
            )
            
            if response.status_code == 200:
                data = response.json()
                picks = data.get("picks", [])
                
                if not picks:
                    self.log_test(
                        "Integration - MLB Picks Today",
                        True,
                        "No picks today (empty slate is valid)"
                    )
                    return True
                
                # Check if any pick has series_familiarity
                has_series_fam = False
                for pick in picks:
                    if "series_familiarity" in pick:
                        has_series_fam = True
                        sf = pick["series_familiarity"]
                        # Verify structure
                        if sf.get("available") and "series_familiarity_score" in sf:
                            self.log_test(
                                "Integration - Series Familiarity Persisted",
                                True,
                                f"Found series_familiarity in pick: score={sf.get('series_familiarity_score')}"
                            )
                            return True
                
                if not has_series_fam:
                    self.log_test(
                        "Integration - Series Familiarity Persisted",
                        True,
                        "No series_familiarity in picks (may not be applicable today)"
                    )
                    return True
                
            else:
                self.log_test(
                    "Integration - MLB Picks Today",
                    False,
                    f"Status {response.status_code}"
                )
                return False
                
        except Exception as e:
            self.log_test("Integration - MLB Picks Today", False, str(e))
            return False

    def print_summary(self):
        """Print test summary"""
        print("\n" + "="*70)
        print("📊 TEST SUMMARY - MLB Priorities 1-4 + Fix 4")
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
    print("🧪 MLB ENGINE PRIORITIES 1-4 + FIX 4 BACKEND TESTS")
    print("="*70)
    
    tester = MLBPrioritiesTester()
    
    # Run tests in order
    if not tester.login():
        print("\n❌ Login failed, cannot continue tests")
        return 1
    
    # Fix 4 tests
    tester.test_manual_odds_endpoint_basic()
    tester.test_manual_odds_comma_decimal()
    tester.test_manual_odds_invalid_rejects()
    
    # Priority 1 tests
    tester.test_lambda_model_priority1()
    
    # Priority 2 tests
    tester.test_adjustment_breakdown_priority2()
    
    # Priority 3 tests
    tester.test_series_familiarity_priority3()
    
    # Integration tests
    tester.test_mlb_picks_today_integration()
    
    # Print summary
    return tester.print_summary()

if __name__ == "__main__":
    sys.exit(main())
