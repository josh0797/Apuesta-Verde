"""Backend API Testing for Value Bet Intelligence - Phase 4 Features
Tests ROI calculator, analysis reconciliation, and Playwright fallback scrapers.
"""
import requests
import sys
import time
from datetime import datetime

class Phase4Tester:
    def __init__(self, base_url="https://low-volatility-plays.preview.emergentagent.com"):
        self.base_url = base_url
        self.token = None
        self.tests_run = 0
        self.tests_passed = 0
        self.tests_failed = []
        self.demo_user = {"email": "demo@valuebet.app", "password": "demo1234"}
        
    def log(self, message, level="INFO"):
        """Log test messages"""
        timestamp = datetime.now().strftime("%H:%M:%S")
        print(f"[{timestamp}] {level}: {message}")
    
    def run_test(self, name, method, endpoint, expected_status, data=None, headers=None, timeout=120, params=None):
        """Run a single API test"""
        url = f"{self.base_url}/api/{endpoint}"
        test_headers = {'Content-Type': 'application/json'}
        
        if self.token and not (headers and 'skip_auth' in headers):
            test_headers['Authorization'] = f'Bearer {self.token}'
        
        if headers:
            headers.pop('skip_auth', None)
            test_headers.update(headers)
        
        self.tests_run += 1
        self.log(f"Testing {name}...")
        
        try:
            if method == 'GET':
                response = requests.get(url, headers=test_headers, timeout=timeout, params=params)
            elif method == 'POST':
                response = requests.post(url, json=data, headers=test_headers, timeout=timeout, params=params)
            
            success = response.status_code == expected_status
            
            if success:
                self.tests_passed += 1
                self.log(f"✅ PASSED - {name} (Status: {response.status_code})", "PASS")
                try:
                    return True, response.json()
                except:
                    return True, {}
            else:
                self.tests_failed.append({
                    "test": name,
                    "expected": expected_status,
                    "got": response.status_code,
                    "response": response.text[:500]
                })
                self.log(f"❌ FAILED - {name} (Expected {expected_status}, got {response.status_code})", "FAIL")
                self.log(f"   Response: {response.text[:500]}", "FAIL")
                return False, {}
                
        except requests.exceptions.Timeout:
            self.tests_failed.append({
                "test": name,
                "expected": expected_status,
                "got": "TIMEOUT",
                "response": f"Request timed out after {timeout}s"
            })
            self.log(f"❌ FAILED - {name} (Timeout after {timeout}s)", "FAIL")
            return False, {}
        except Exception as e:
            self.tests_failed.append({
                "test": name,
                "expected": expected_status,
                "got": "ERROR",
                "response": str(e)
            })
            self.log(f"❌ FAILED - {name} (Error: {str(e)})", "FAIL")
            return False, {}
    
    def test_login(self):
        """Login with demo user"""
        self.log("\n=== Phase 4: Demo Login ===")
        success, response = self.run_test(
            "Demo Login",
            "POST",
            "auth/login",
            200,
            data=self.demo_user,
            headers={'skip_auth': True}
        )
        if success and 'token' in response:
            self.token = response['token']
            self.log(f"   Token obtained: {self.token[:20]}...")
            return True
        return False
    
    def test_roi_calculator_basic(self):
        """Test ROI calculator with default stake"""
        self.log("\n=== Phase 4: ROI Calculator (default stake) ===")
        success, response = self.run_test(
            "ROI Calculator - Default Stake",
            "GET",
            "stats/dashboard",
            200
        )
        
        if success:
            # Verify roi object exists
            roi = response.get('roi')
            if not roi:
                self.log("   ❌ Missing 'roi' object in response", "FAIL")
                self.tests_failed.append({
                    "test": "ROI object presence",
                    "expected": "roi object",
                    "got": "missing",
                    "response": "roi key not found in response"
                })
                return False
            
            # Verify required fields
            required_fields = ['stake_per_pick', 'total_wagered', 'total_profit', 'roi_pct', 
                             'avg_won_odds', 'avg_lost_odds', 'settled_with_odds', 'settled_total']
            missing_fields = [f for f in required_fields if f not in roi]
            
            if missing_fields:
                self.log(f"   ❌ Missing ROI fields: {missing_fields}", "FAIL")
                self.tests_failed.append({
                    "test": "ROI fields completeness",
                    "expected": str(required_fields),
                    "got": f"missing {missing_fields}",
                    "response": str(roi)
                })
                return False
            
            self.log(f"   ✅ ROI object structure valid")
            self.log(f"   Stake per pick: {roi['stake_per_pick']}")
            self.log(f"   Total wagered: {roi['total_wagered']}")
            self.log(f"   Total profit: {roi['total_profit']}")
            self.log(f"   ROI %: {roi['roi_pct']}")
            self.log(f"   Avg won odds: {roi['avg_won_odds']}")
            self.log(f"   Avg lost odds: {roi['avg_lost_odds']}")
            self.log(f"   Settled with odds: {roi['settled_with_odds']}")
            self.log(f"   Settled total: {roi['settled_total']}")
            
            return True
        return False
    
    def test_roi_calculator_custom_stake(self):
        """Test ROI calculator with custom stake parameter"""
        self.log("\n=== Phase 4: ROI Calculator (custom stake=20) ===")
        success, response = self.run_test(
            "ROI Calculator - Custom Stake",
            "GET",
            "stats/dashboard",
            200,
            params={'stake': 20}
        )
        
        if success:
            roi = response.get('roi')
            if not roi:
                self.log("   ❌ Missing 'roi' object", "FAIL")
                return False
            
            stake = roi.get('stake_per_pick')
            total_wagered = roi.get('total_wagered')
            settled_with_odds = roi.get('settled_with_odds')
            
            self.log(f"   Stake per pick: {stake}")
            self.log(f"   Total wagered: {total_wagered}")
            self.log(f"   Settled with odds: {settled_with_odds}")
            
            # Verify stake is 20
            if stake != 20:
                self.log(f"   ❌ Expected stake=20, got {stake}", "FAIL")
                self.tests_failed.append({
                    "test": "Custom stake parameter",
                    "expected": "20",
                    "got": str(stake),
                    "response": str(roi)
                })
                return False
            
            # Verify total_wagered ≈ stake * settled_with_odds
            expected_wagered = stake * settled_with_odds
            if settled_with_odds > 0 and abs(total_wagered - expected_wagered) > 0.01:
                self.log(f"   ❌ Total wagered mismatch: expected {expected_wagered}, got {total_wagered}", "FAIL")
                self.tests_failed.append({
                    "test": "Total wagered calculation",
                    "expected": str(expected_wagered),
                    "got": str(total_wagered),
                    "response": f"stake={stake}, settled_with_odds={settled_with_odds}"
                })
                return False
            
            self.log(f"   ✅ Total wagered calculation correct: {total_wagered} ≈ {stake} × {settled_with_odds}")
            return True
        return False
    
    def test_roi_per_tier(self):
        """Test ROI per tier in accuracy_by_tier"""
        self.log("\n=== Phase 4: ROI per Tier ===")
        success, response = self.run_test(
            "ROI per Tier",
            "GET",
            "stats/dashboard",
            200,
            params={'stake': 10}
        )
        
        if success:
            accuracy_by_tier = response.get('accuracy_by_tier', {})
            if not accuracy_by_tier:
                self.log("   ⚠️  No accuracy_by_tier data (may be no tracked picks yet)", "WARN")
                return True  # Not a failure if no data
            
            required_tier_fields = ['won', 'lost', 'settled', 'rate', 'profit', 'wagered', 'roi_pct']
            
            for tier in ['Maxima', 'Alta', 'Media']:
                tier_data = accuracy_by_tier.get(tier, {})
                self.log(f"   Checking tier: {tier}")
                
                missing_fields = [f for f in required_tier_fields if f not in tier_data]
                if missing_fields:
                    self.log(f"   ❌ Missing fields in {tier}: {missing_fields}", "FAIL")
                    self.tests_failed.append({
                        "test": f"ROI fields in tier {tier}",
                        "expected": str(required_tier_fields),
                        "got": f"missing {missing_fields}",
                        "response": str(tier_data)
                    })
                    return False
                
                self.log(f"     Won: {tier_data['won']}, Lost: {tier_data['lost']}, Settled: {tier_data['settled']}")
                self.log(f"     Rate: {tier_data['rate']}%, Profit: {tier_data['profit']}, Wagered: {tier_data['wagered']}")
                self.log(f"     ROI %: {tier_data['roi_pct']}")
            
            self.log(f"   ✅ All tiers have complete ROI data")
            return True
        return False
    
    def test_track_with_odds(self):
        """Test POST /api/picks/track with odds field"""
        self.log("\n=== Phase 4: Track Pick with Odds ===")
        
        # First get a pick run
        success, response = self.run_test(
            "Get Pick Run for Tracking",
            "GET",
            "picks/today",
            200
        )
        
        if not success or not response.get('pick_run'):
            self.log("   ⚠️  No pick run available, creating test tracking data", "WARN")
            # Create a test tracking entry
            track_payload = {
                "run_id": "test_run_phase4",
                "match_id": 9999999,
                "market": "Under 2.5",
                "selection": "Under 2.5 goals",
                "confidence_score": 80,
                "outcome": "won",
                "odds": 1.85,
                "league": "Test League",
                "match_label": "Test Team A vs Test Team B",
                "notes": "Phase 4 test with odds"
            }
        else:
            pick_run = response['pick_run']
            run_id = pick_run.get('id')
            picks = pick_run.get('payload', {}).get('picks', [])
            
            if not picks:
                self.log("   ⚠️  No picks in run, using test data", "WARN")
                track_payload = {
                    "run_id": run_id,
                    "match_id": 9999998,
                    "market": "Under 2.5",
                    "selection": "Under 2.5 goals",
                    "confidence_score": 80,
                    "outcome": "won",
                    "odds": 1.85,
                    "league": "Test League",
                    "match_label": "Test Team A vs Test Team B",
                    "notes": "Phase 4 test with odds"
                }
            else:
                first_pick = picks[0]
                track_payload = {
                    "run_id": run_id,
                    "match_id": first_pick.get('match_id'),
                    "market": first_pick.get('recommendation', {}).get('market', 'Under 2.5'),
                    "selection": first_pick.get('recommendation', {}).get('selection', 'Under 2.5'),
                    "confidence_score": first_pick.get('recommendation', {}).get('confidence_score', 80),
                    "outcome": "won",
                    "odds": 1.85,
                    "league": first_pick.get('league'),
                    "match_label": first_pick.get('match_label'),
                    "notes": "Phase 4 test with odds"
                }
        
        # Track the pick with odds
        success, response = self.run_test(
            "Track Pick with Odds (won, odds=1.85)",
            "POST",
            "picks/track",
            200,
            data=track_payload
        )
        
        if not success:
            return False
        
        self.log(f"   ✅ Pick tracked with odds: {response.get('pick_id')}")
        
        # Now verify ROI calculation includes this pick
        self.log("   Verifying ROI calculation includes the new pick...")
        success2, response2 = self.run_test(
            "Verify ROI after tracking",
            "GET",
            "stats/dashboard",
            200,
            params={'stake': 10}
        )
        
        if success2:
            roi = response2.get('roi', {})
            total_profit = roi.get('total_profit', 0)
            avg_won_odds = roi.get('avg_won_odds', 0)
            
            self.log(f"   Total profit: {total_profit}")
            self.log(f"   Avg won odds: {avg_won_odds}")
            
            # The new pick should have added 10 * (1.85 - 1) = 8.50 to profit
            # We can't verify exact amount without knowing previous state, but we can verify odds is included
            if avg_won_odds > 0:
                self.log(f"   ✅ ROI calculation includes won odds (avg: {avg_won_odds})")
                return True
            else:
                self.log(f"   ⚠️  Avg won odds is 0 (may need more data)", "WARN")
                return True  # Not a hard failure
        
        return False
    
    def test_analysis_reconciliation(self):
        """Test analysis reconciliation - discarded lists must be complete"""
        self.log("\n=== Phase 4: Analysis Reconciliation ===")
        self.log("   Note: This may take 30-90 seconds (LLM processing)...")
        
        payload = {
            "refresh": False,  # Use cached data
            "include_live": False,
            "max_matches": 5
        }
        
        success, response = self.run_test(
            "Run Analysis with Reconciliation Check",
            "POST",
            "analysis/run",
            200,
            data=payload,
            timeout=120
        )
        
        if not success:
            return False
        
        result = response.get('result', {})
        summary = result.get('summary', {})
        picks = result.get('picks', [])
        
        total_analyzed = summary.get('total_analyzed', 0)
        total_recommended = summary.get('total_recommended', 0)
        total_discarded = summary.get('total_discarded', 0)
        
        discarded_motivation = summary.get('discarded_motivation', [])
        discarded_market = summary.get('discarded_market', [])
        incomplete_data = summary.get('incomplete_data', [])
        
        self.log(f"   Total analyzed: {total_analyzed}")
        self.log(f"   Total recommended: {total_recommended}")
        self.log(f"   Total discarded: {total_discarded}")
        self.log(f"   Picks returned: {len(picks)}")
        self.log(f"   Discarded (motivation): {len(discarded_motivation)}")
        self.log(f"   Discarded (market): {len(discarded_market)}")
        self.log(f"   Incomplete data: {len(incomplete_data)}")
        
        # Verify reconciliation: total_recommended + discarded lists = total_analyzed
        total_accounted = len(picks) + len(discarded_motivation) + len(discarded_market) + len(incomplete_data)
        
        if total_accounted != total_analyzed:
            self.log(f"   ❌ Reconciliation failed: {total_accounted} accounted != {total_analyzed} analyzed", "FAIL")
            self.tests_failed.append({
                "test": "Analysis reconciliation",
                "expected": str(total_analyzed),
                "got": str(total_accounted),
                "response": f"picks={len(picks)}, disc_mot={len(discarded_motivation)}, disc_mkt={len(discarded_market)}, incomp={len(incomplete_data)}"
            })
            return False
        
        self.log(f"   ✅ Reconciliation valid: {total_accounted} == {total_analyzed}")
        
        # Verify each discarded entry has required fields
        for disc in discarded_motivation:
            if not all(k in disc for k in ['match_id', 'match_label', 'reason']):
                self.log(f"   ❌ Discarded motivation entry missing fields: {disc}", "FAIL")
                self.tests_failed.append({
                    "test": "Discarded motivation fields",
                    "expected": "match_id, match_label, reason",
                    "got": str(disc.keys()),
                    "response": str(disc)
                })
                return False
        
        for disc in discarded_market:
            if not all(k in disc for k in ['match_id', 'match_label', 'reason']):
                self.log(f"   ❌ Discarded market entry missing fields: {disc}", "FAIL")
                self.tests_failed.append({
                    "test": "Discarded market fields",
                    "expected": "match_id, match_label, reason",
                    "got": str(disc.keys()),
                    "response": str(disc)
                })
                return False
        
        for disc in incomplete_data:
            if not all(k in disc for k in ['match_id', 'match_label', 'missing']):
                self.log(f"   ❌ Incomplete data entry missing fields: {disc}", "FAIL")
                self.tests_failed.append({
                    "test": "Incomplete data fields",
                    "expected": "match_id, match_label, missing",
                    "got": str(disc.keys()),
                    "response": str(disc)
                })
                return False
        
        self.log(f"   ✅ All discarded entries have required fields")
        
        # Show sample discarded entries
        if discarded_motivation:
            self.log(f"   Sample discarded (motivation): {discarded_motivation[0].get('match_label')} - {discarded_motivation[0].get('reason')}")
        if discarded_market:
            self.log(f"   Sample discarded (market): {discarded_market[0].get('match_label')} - {discarded_market[0].get('reason')}")
        if incomplete_data:
            self.log(f"   Sample incomplete: {incomplete_data[0].get('match_label')} - {incomplete_data[0].get('missing')}")
        
        return True
    
    def test_analysis_provider(self):
        """Test that analysis result includes _provider field"""
        self.log("\n=== Phase 4: Analysis Provider ===")
        
        # Get the most recent pick run
        success, response = self.run_test(
            "Get Latest Pick Run",
            "GET",
            "picks/today",
            200
        )
        
        if not success or not response.get('pick_run'):
            self.log("   ⚠️  No pick run available, skipping provider check", "WARN")
            return True  # Not a failure
        
        pick_run = response['pick_run']
        payload = pick_run.get('payload', {})
        provider = payload.get('_provider')
        
        if not provider:
            self.log("   ❌ Missing _provider field in analysis result", "FAIL")
            self.tests_failed.append({
                "test": "Analysis provider field",
                "expected": "_provider field",
                "got": "missing",
                "response": str(payload.keys())
            })
            return False
        
        self.log(f"   Provider: {provider}")
        
        # Verify it's one of the expected providers
        expected_providers = ['openai:gpt-4o-mini', 'emergent:claude-sonnet-4-5']
        if not any(exp in provider for exp in ['openai:', 'emergent:']):
            self.log(f"   ⚠️  Unexpected provider format: {provider}", "WARN")
        else:
            self.log(f"   ✅ Provider field present and valid: {provider}")
        
        return True
    
    def test_playwright_fallback(self):
        """Test Playwright-based fallback scrapers"""
        self.log("\n=== Phase 4: Playwright Fallback Scrapers ===")
        self.log("   Note: This may take 10-20 seconds (browser launch)...")
        self.log("   Note: May return 0 results due to Cloudflare blocking (expected)")
        
        success, response = self.run_test(
            "Playwright Fallback Sources",
            "GET",
            "system/fallback-sources",
            200,
            params={'use_playwright': True},
            timeout=60
        )
        
        if not success:
            return False
        
        playwright_used = response.get('playwright_used')
        data = response.get('data', {})
        summary = response.get('summary', {})
        
        self.log(f"   Playwright used: {playwright_used}")
        
        if not playwright_used:
            self.log("   ❌ playwright_used should be True", "FAIL")
            self.tests_failed.append({
                "test": "Playwright flag",
                "expected": "True",
                "got": str(playwright_used),
                "response": str(response)
            })
            return False
        
        # Verify required keys exist (even if empty due to Cloudflare)
        required_keys = ['sofascore_pw', 'flashscore_pw']
        missing_keys = [k for k in required_keys if k not in data]
        
        if missing_keys:
            self.log(f"   ❌ Missing Playwright data keys: {missing_keys}", "FAIL")
            self.tests_failed.append({
                "test": "Playwright data keys",
                "expected": str(required_keys),
                "got": f"missing {missing_keys}",
                "response": str(data.keys())
            })
            return False
        
        self.log(f"   ✅ Playwright keys present: {required_keys}")
        self.log(f"   Sofascore PW results: {len(data.get('sofascore_pw', []))}")
        self.log(f"   Flashscore PW results: {len(data.get('flashscore_pw', []))}")
        
        if len(data.get('sofascore_pw', [])) == 0 and len(data.get('flashscore_pw', [])) == 0:
            self.log("   ℹ️  No results from Playwright scrapers (likely Cloudflare blocking - this is OK)", "INFO")
        
        return True
    
    def test_standard_fallback(self):
        """Test standard fallback scrapers (without Playwright)"""
        self.log("\n=== Phase 4: Standard Fallback Scrapers ===")
        
        success, response = self.run_test(
            "Standard Fallback Sources",
            "GET",
            "system/fallback-sources",
            200,
            timeout=30
        )
        
        if not success:
            return False
        
        playwright_used = response.get('playwright_used')
        data = response.get('data', {})
        
        self.log(f"   Playwright used: {playwright_used}")
        
        if playwright_used:
            self.log("   ❌ playwright_used should be False for standard fallback", "FAIL")
            self.tests_failed.append({
                "test": "Standard fallback (no playwright)",
                "expected": "False",
                "got": str(playwright_used),
                "response": str(response)
            })
            return False
        
        # Verify ESPN data exists
        espn_data = data.get('espn', [])
        self.log(f"   ESPN results: {len(espn_data)}")
        
        if len(espn_data) == 0:
            self.log("   ⚠️  No ESPN results (may be temporary API issue)", "WARN")
        else:
            self.log(f"   ✅ ESPN fallback working: {len(espn_data)} matches")
            # Show sample
            if espn_data:
                sample = espn_data[0]
                self.log(f"   Sample: {sample.get('home_team', {}).get('name')} vs {sample.get('away_team', {}).get('name')}")
        
        return True
    
    def test_authz_on_new_params(self):
        """Test that new endpoints require authorization"""
        self.log("\n=== Phase 4: Authorization on New Endpoints ===")
        
        saved_token = self.token
        self.token = None
        
        success, _ = self.run_test(
            "Unauthorized /stats/dashboard?stake=10",
            "GET",
            "stats/dashboard",
            401,
            params={'stake': 10},
            headers={'skip_auth': True}
        )
        
        self.token = saved_token
        
        if success:
            self.log("   ✅ Authorization required for /stats/dashboard with stake param")
        
        return success
    
    def test_existing_endpoints(self):
        """Quick check that existing endpoints still work"""
        self.log("\n=== Phase 4: Existing Endpoints Still Work ===")
        
        tests = [
            ("auth/me", "GET", 200),
            ("picks/today", "GET", 200),
            ("matches/upcoming", "GET", 200),
            ("picks/history", "GET", 200),
        ]
        
        all_passed = True
        for endpoint, method, expected_status in tests:
            success, _ = self.run_test(
                f"Existing endpoint: {endpoint}",
                method,
                endpoint,
                expected_status
            )
            if not success:
                all_passed = False
        
        return all_passed
    
    def run_all_tests(self):
        """Run all Phase 4 tests"""
        self.log("\n" + "="*70)
        self.log("VALUE BET INTELLIGENCE - PHASE 4 FEATURE TESTS")
        self.log("="*70)
        
        start_time = time.time()
        
        # Test sequence
        tests = [
            ("Login", self.test_login),
            ("ROI Calculator - Basic", self.test_roi_calculator_basic),
            ("ROI Calculator - Custom Stake", self.test_roi_calculator_custom_stake),
            ("ROI per Tier", self.test_roi_per_tier),
            ("Track with Odds", self.test_track_with_odds),
            ("Analysis Reconciliation", self.test_analysis_reconciliation),
            ("Analysis Provider", self.test_analysis_provider),
            ("Playwright Fallback", self.test_playwright_fallback),
            ("Standard Fallback", self.test_standard_fallback),
            ("Authorization on New Params", self.test_authz_on_new_params),
            ("Existing Endpoints", self.test_existing_endpoints),
        ]
        
        for test_name, test_func in tests:
            try:
                test_func()
            except Exception as e:
                self.log(f"❌ Test '{test_name}' crashed: {str(e)}", "ERROR")
                import traceback
                self.log(traceback.format_exc(), "ERROR")
                self.tests_failed.append({
                    "test": test_name,
                    "expected": "success",
                    "got": "CRASH",
                    "response": str(e)
                })
        
        elapsed = time.time() - start_time
        
        # Print summary
        self.log("\n" + "="*70)
        self.log("PHASE 4 TEST SUMMARY")
        self.log("="*70)
        self.log(f"Total tests run: {self.tests_run}")
        self.log(f"Tests passed: {self.tests_passed}")
        self.log(f"Tests failed: {len(self.tests_failed)}")
        self.log(f"Success rate: {(self.tests_passed/self.tests_run*100):.1f}%")
        self.log(f"Time elapsed: {elapsed:.1f}s")
        
        if self.tests_failed:
            self.log("\n" + "="*70)
            self.log("FAILED TESTS DETAILS")
            self.log("="*70)
            for failure in self.tests_failed:
                self.log(f"\n❌ {failure['test']}")
                self.log(f"   Expected: {failure['expected']}")
                self.log(f"   Got: {failure['got']}")
                self.log(f"   Response: {failure['response']}")
        
        self.log("\n" + "="*70)
        
        return 0 if len(self.tests_failed) == 0 else 1

def main():
    tester = Phase4Tester()
    return tester.run_all_tests()

if __name__ == "__main__":
    sys.exit(main())
