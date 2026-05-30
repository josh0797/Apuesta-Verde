#!/usr/bin/env python3
"""Backend test suite for Live Corner Pressure Intelligence feature.

Tests:
  1. Pure function evaluate_live_corner_market() — Tests A/B/C/D
  2. Storage layer functions
  3. API endpoints (POST territorial_control, GET corner_intelligence/history)
  4. Regression: existing territorial control features
"""
import sys
import os
import asyncio
import requests
from datetime import datetime

# Add backend to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'backend'))

# Backend URL from environment
BACKEND_URL = os.environ.get('REACT_APP_BACKEND_URL', 'https://low-volatility-plays.preview.emergentagent.com')
API_BASE = f"{BACKEND_URL}/api"

class TestResults:
    def __init__(self):
        self.total = 0
        self.passed = 0
        self.failed = 0
        self.errors = []
    
    def record_pass(self, test_name):
        self.total += 1
        self.passed += 1
        print(f"✅ PASS: {test_name}")
    
    def record_fail(self, test_name, reason):
        self.total += 1
        self.failed += 1
        self.errors.append(f"{test_name}: {reason}")
        print(f"❌ FAIL: {test_name}")
        print(f"   Reason: {reason}")
    
    def summary(self):
        print("\n" + "="*70)
        print(f"TEST SUMMARY: {self.passed}/{self.total} passed")
        if self.errors:
            print("\nFailed tests:")
            for err in self.errors:
                print(f"  • {err}")
        print("="*70)
        return self.failed == 0


def test_pure_functions():
    """Test the pure function evaluate_live_corner_market() with all 4 acceptance tests."""
    print("\n" + "="*70)
    print("TESTING PURE FUNCTIONS: evaluate_live_corner_market()")
    print("="*70)
    
    results = TestResults()
    
    try:
        from services.live_corner_engine import evaluate_live_corner_market
        
        # ═══════════════════════════════════════════════════════════════════
        # TEST A — PSG canonical case
        # ═══════════════════════════════════════════════════════════════════
        print("\n[TEST A] PSG canonical case (should recommend)")
        metrics_a = {
            'minute': 65,
            'score_home': 1,
            'score_away': 1,
            'home_team': 'PSG',
            'away_team': 'Arsenal',
            'possession_home': 75,
            'possession_away': 25,
            'shots_home': 8,
            'shots_away': 2,
            'shots_on_target_home': 3,
            'shots_on_target_away': 1,
            'corners_home': 5,
            'corners_away': 0,
            'xg_home': 0.95,
            'xg_away': 0.25,
            'xt_home': 32,
            'xt_away': 8,
            'dangerous_attacks_home': 16,
            'dangerous_attacks_away': 3,
        }
        
        result_a = evaluate_live_corner_market(metrics_a, surface_threshold=55)
        
        # Validate TEST A expectations
        checks = []
        checks.append(('should_recommend=True', result_a.get('should_recommend') == True))
        checks.append(('recommended_market contains "Total Corners Over"', 
                      result_a.get('recommended_market') and 'Total Corners Over' in result_a['recommended_market']))
        checks.append(('confidence >= 75', result_a.get('confidence', 0) >= 75))
        checks.append(('risk in {LOW, MEDIUM}', result_a.get('risk') in ['LOW', 'MEDIUM']))
        checks.append(('classification.psg_benchmark=True', 
                      result_a.get('classification', {}).get('psg_benchmark') == True))
        checks.append(('classification.tc_with_corner_pressure=True',
                      result_a.get('classification', {}).get('tc_with_corner_pressure') == True))
        
        reason_codes = result_a.get('reason_codes', [])
        checks.append(('HIGH_TERRITORIAL_DOMINANCE in reason_codes',
                      'HIGH_TERRITORIAL_DOMINANCE' in reason_codes))
        checks.append(('CONTROLLING_TEAM_NEEDS_GOAL in reason_codes',
                      'CONTROLLING_TEAM_NEEDS_GOAL' in reason_codes))
        checks.append(('PSG_ARSENAL_BENCHMARK_MATCH in reason_codes',
                      'PSG_ARSENAL_BENCHMARK_MATCH' in reason_codes))
        
        all_passed = all(check[1] for check in checks)
        if all_passed:
            results.record_pass("TEST A - PSG canonical")
            print(f"   ✓ should_recommend: {result_a.get('should_recommend')}")
            print(f"   ✓ recommended_market: {result_a.get('recommended_market')}")
            print(f"   ✓ confidence: {result_a.get('confidence')}")
            print(f"   ✓ risk: {result_a.get('risk')}")
            print(f"   ✓ reason_codes: {reason_codes}")
        else:
            failed_checks = [c[0] for c in checks if not c[1]]
            results.record_fail("TEST A - PSG canonical", f"Failed checks: {', '.join(failed_checks)}")
            print(f"   Result: {result_a}")
        
        # ═══════════════════════════════════════════════════════════════════
        # TEST B — High possession but no corners
        # ═══════════════════════════════════════════════════════════════════
        print("\n[TEST B] High possession but no corners (should NOT recommend)")
        metrics_b = {
            'minute': 45,
            'score_home': 0,
            'score_away': 0,
            'home_team': 'Team A',
            'away_team': 'Team B',
            'possession_home': 75,
            'possession_away': 25,
            'shots_home': 2,
            'shots_away': 1,
            'shots_on_target_home': 1,
            'shots_on_target_away': 0,
            'corners_home': 0,
            'corners_away': 0,
            'xg_home': 0.10,
            'xg_away': 0.05,
            'xt_home': 10,
            'xt_away': 3,
            'dangerous_attacks_home': 4,
            'dangerous_attacks_away': 1,
        }
        
        result_b = evaluate_live_corner_market(metrics_b, surface_threshold=55)
        
        if result_b.get('should_recommend') == False:
            results.record_pass("TEST B - No corners")
            print(f"   ✓ should_recommend: False (as expected)")
            print(f"   ✓ corner_pressure_score: {result_b.get('corner_pressure_score')}")
        else:
            results.record_fail("TEST B - No corners", 
                              f"Expected should_recommend=False, got {result_b.get('should_recommend')}")
        
        # ═══════════════════════════════════════════════════════════════════
        # TEST C — High xG pressure (should NOT recommend corners)
        # ═══════════════════════════════════════════════════════════════════
        print("\n[TEST C] High xG pressure (should NOT recommend corners)")
        metrics_c = {
            'minute': 60,
            'score_home': 0,
            'score_away': 0,
            'home_team': 'Team X',
            'away_team': 'Team Y',
            'possession_home': 70,
            'possession_away': 30,
            'shots_home': 9,
            'shots_away': 2,
            'shots_on_target_home': 5,
            'shots_on_target_away': 1,
            'corners_home': 3,
            'corners_away': 1,
            'xg_home': 1.5,
            'xg_away': 0.20,
            'xt_home': 28,
            'xt_away': 8,
            'dangerous_attacks_home': 12,
            'dangerous_attacks_away': 3,
        }
        
        result_c = evaluate_live_corner_market(metrics_c, surface_threshold=55)
        
        # Should NOT recommend because xG > 0.80 disables CWGD
        if result_c.get('should_recommend') == False:
            results.record_pass("TEST C - High xG")
            print(f"   ✓ should_recommend: False (xG too high for corner recommendation)")
            print(f"   ✓ xG: {metrics_c['xg_home']}")
        else:
            results.record_fail("TEST C - High xG",
                              f"Expected should_recommend=False (high xG), got {result_c.get('should_recommend')}")
        
        # ═══════════════════════════════════════════════════════════════════
        # TEST D — Leading team with corner dominance (downgrade)
        # ═══════════════════════════════════════════════════════════════════
        print("\n[TEST D] Leading team corner dominance (should downgrade)")
        metrics_d = {
            'minute': 70,
            'score_home': 2,
            'score_away': 0,
            'home_team': 'Team M',
            'away_team': 'Team N',
            'possession_home': 68,
            'possession_away': 32,
            'shots_home': 7,
            'shots_away': 2,
            'shots_on_target_home': 3,
            'shots_on_target_away': 1,
            'corners_home': 5,
            'corners_away': 0,
            'xg_home': 1.2,
            'xg_away': 0.15,
            'xt_home': 25,
            'xt_away': 7,
            'dangerous_attacks_home': 10,
            'dangerous_attacks_away': 2,
        }
        
        result_d = evaluate_live_corner_market(metrics_d, surface_threshold=55)
        
        checks_d = []
        checks_d.append(('should_recommend=False', result_d.get('should_recommend') == False))
        checks_d.append(('classification.downgrade_due_to_lead=True',
                        result_d.get('classification', {}).get('downgrade_due_to_lead') == True))
        
        if all(c[1] for c in checks_d):
            results.record_pass("TEST D - Downgrade when leading")
            print(f"   ✓ should_recommend: False (downgrade applied)")
            print(f"   ✓ downgrade_due_to_lead: True")
        else:
            failed = [c[0] for c in checks_d if not c[1]]
            results.record_fail("TEST D - Downgrade when leading", f"Failed: {', '.join(failed)}")
        
        # ═══════════════════════════════════════════════════════════════════
        # TEST avoid_markets logic
        # ═══════════════════════════════════════════════════════════════════
        print("\n[TEST] avoid_markets logic")
        # Use TEST A metrics (control_without_goal_depth should be true)
        avoid_markets = result_a.get('avoid_markets', [])
        expected_avoid = ['Over 2.5 goles', 'Ambos equipos anotan: Sí', 'Siguiente gol']
        
        has_avoid = any(market in avoid_markets for market in expected_avoid)
        if has_avoid:
            results.record_pass("avoid_markets logic")
            print(f"   ✓ avoid_markets: {avoid_markets}")
        else:
            results.record_fail("avoid_markets logic",
                              f"Expected some of {expected_avoid}, got {avoid_markets}")
        
    except Exception as exc:
        results.record_fail("Pure function tests", f"Exception: {exc}")
        import traceback
        traceback.print_exc()
    
    return results


def test_api_endpoints():
    """Test the API endpoints."""
    print("\n" + "="*70)
    print("TESTING API ENDPOINTS")
    print("="*70)
    
    results = TestResults()
    
    # Login first
    print("\n[AUTH] Logging in as demo user...")
    try:
        login_resp = requests.post(
            f"{API_BASE}/auth/login",
            json={"email": "demo@valuebet.app", "password": "demo1234"},
            timeout=10
        )
        if login_resp.status_code != 200:
            results.record_fail("Login", f"Status {login_resp.status_code}: {login_resp.text}")
            return results
        
        token = login_resp.json().get('token')
        if not token:
            results.record_fail("Login", "No token in response")
            return results
        
        results.record_pass("Login")
        headers = {"Authorization": f"Bearer {token}"}
        
    except Exception as exc:
        results.record_fail("Login", f"Exception: {exc}")
        return results
    
    # ═══════════════════════════════════════════════════════════════════
    # TEST POST /api/football/live/territorial_control with PSG metrics
    # ═══════════════════════════════════════════════════════════════════
    print("\n[API] POST /api/football/live/territorial_control (PSG canonical)")
    try:
        psg_metrics = {
            'minute': 65,
            'score_home': 1,
            'score_away': 1,
            'home_team': 'PSG',
            'away_team': 'Arsenal',
            'possession_home': 75,
            'possession_away': 25,
            'shots_home': 8,
            'shots_away': 2,
            'shots_on_target_home': 3,
            'shots_on_target_away': 1,
            'corners_home': 5,
            'corners_away': 0,
            'xg_home': 0.95,
            'xg_away': 0.25,
            'xt_home': 32,
            'xt_away': 8,
            'dangerous_attacks_home': 16,
            'dangerous_attacks_away': 3,
        }
        
        resp = requests.post(
            f"{API_BASE}/football/live/territorial_control",
            json={"metrics": psg_metrics, "surface_threshold": 55},
            headers=headers,
            timeout=15
        )
        
        if resp.status_code != 200:
            results.record_fail("POST territorial_control",
                              f"Status {resp.status_code}: {resp.text}")
        else:
            data = resp.json()
            
            # Check response structure
            checks = []
            checks.append(('ok=true', data.get('ok') == True))
            checks.append(('has corner_recommendation', 'corner_recommendation' in data))
            checks.append(('has corner_evaluation_id', 'corner_evaluation_id' in data))
            checks.append(('has territorial', 'territorial' in data))
            checks.append(('has corner', 'corner' in data))
            checks.append(('has ranked_markets', 'ranked_markets' in data))
            
            corner_rec = data.get('corner_recommendation', {})
            checks.append(('corner_recommendation.should_recommend=true',
                          corner_rec.get('should_recommend') == True))
            checks.append(('corner_recommendation.recommended_market starts with "Total Corners Over"',
                          corner_rec.get('recommended_market', '').startswith('Total Corners Over')))
            
            if all(c[1] for c in checks):
                results.record_pass("POST territorial_control")
                print(f"   ✓ corner_recommendation.should_recommend: {corner_rec.get('should_recommend')}")
                print(f"   ✓ corner_recommendation.recommended_market: {corner_rec.get('recommended_market')}")
                print(f"   ✓ corner_evaluation_id: {data.get('corner_evaluation_id')}")
            else:
                failed = [c[0] for c in checks if not c[1]]
                results.record_fail("POST territorial_control", f"Failed: {', '.join(failed)}")
                print(f"   Response: {data}")
        
    except Exception as exc:
        results.record_fail("POST territorial_control", f"Exception: {exc}")
        import traceback
        traceback.print_exc()
    
    # ═══════════════════════════════════════════════════════════════════
    # TEST GET /api/football/live/corner_intelligence/history
    # ═══════════════════════════════════════════════════════════════════
    print("\n[API] GET /api/football/live/corner_intelligence/history")
    try:
        resp = requests.get(
            f"{API_BASE}/football/live/corner_intelligence/history",
            headers=headers,
            params={"limit": 10},
            timeout=10
        )
        
        if resp.status_code != 200:
            results.record_fail("GET corner_intelligence/history",
                              f"Status {resp.status_code}: {resp.text}")
        else:
            data = resp.json()
            
            checks = []
            checks.append(('ok=true', data.get('ok') == True))
            checks.append(('has count', 'count' in data))
            checks.append(('has items', 'items' in data))
            checks.append(('items is list', isinstance(data.get('items'), list)))
            
            if all(c[1] for c in checks):
                results.record_pass("GET corner_intelligence/history")
                print(f"   ✓ count: {data.get('count')}")
                print(f"   ✓ items: {len(data.get('items', []))} documents")
                
                # Check first item structure if available
                if data.get('items'):
                    item = data['items'][0]
                    required_fields = ['id', 'user_id', 'match_id', 'minute', 'recommended_market',
                                     'confidence', 'risk', 'corner_pressure_score', 'classification',
                                     'reason_codes', 'human_reasons', 'explanation', 'avoid_markets']
                    missing = [f for f in required_fields if f not in item]
                    if missing:
                        print(f"   ⚠ Missing fields in item: {missing}")
                    else:
                        print(f"   ✓ First item has all required fields")
            else:
                failed = [c[0] for c in checks if not c[1]]
                results.record_fail("GET corner_intelligence/history", f"Failed: {', '.join(failed)}")
        
    except Exception as exc:
        results.record_fail("GET corner_intelligence/history", f"Exception: {exc}")
        import traceback
        traceback.print_exc()
    
    # ═══════════════════════════════════════════════════════════════════
    # TEST Regression: existing territorial control features
    # ═══════════════════════════════════════════════════════════════════
    print("\n[REGRESSION] Existing territorial control features")
    try:
        # Use simple metrics
        simple_metrics = {
            'minute': 30,
            'score_home': 0,
            'score_away': 0,
            'home_team': 'Team A',
            'away_team': 'Team B',
            'possession_home': 55,
            'possession_away': 45,
            'shots_home': 3,
            'shots_away': 2,
            'corners_home': 2,
            'corners_away': 1,
            'xt_home': 12,
            'xt_away': 10,
        }
        
        resp = requests.post(
            f"{API_BASE}/football/live/territorial_control",
            json={"metrics": simple_metrics},
            headers=headers,
            timeout=15
        )
        
        if resp.status_code != 200:
            results.record_fail("Regression test",
                              f"Status {resp.status_code}: {resp.text}")
        else:
            data = resp.json()
            
            # Check that old fields still exist
            checks = []
            checks.append(('has territorial', 'territorial' in data))
            checks.append(('has corner', 'corner' in data))
            checks.append(('has ranked_markets', 'ranked_markets' in data))
            checks.append(('has evaluation_id', 'evaluation_id' in data))
            
            if all(c[1] for c in checks):
                results.record_pass("Regression test")
                print(f"   ✓ All existing fields present")
            else:
                failed = [c[0] for c in checks if not c[1]]
                results.record_fail("Regression test", f"Failed: {', '.join(failed)}")
        
    except Exception as exc:
        results.record_fail("Regression test", f"Exception: {exc}")
    
    return results


def main():
    print("\n" + "="*70)
    print("LIVE CORNER PRESSURE INTELLIGENCE — BACKEND TEST SUITE")
    print("="*70)
    print(f"Backend URL: {BACKEND_URL}")
    print(f"Time: {datetime.now().isoformat()}")
    
    # Run pure function tests
    pure_results = test_pure_functions()
    
    # Run API tests
    api_results = test_api_endpoints()
    
    # Combined summary
    print("\n" + "="*70)
    print("FINAL SUMMARY")
    print("="*70)
    total_passed = pure_results.passed + api_results.passed
    total_tests = pure_results.total + api_results.total
    print(f"Total: {total_passed}/{total_tests} tests passed")
    print(f"  Pure functions: {pure_results.passed}/{pure_results.total}")
    print(f"  API endpoints: {api_results.passed}/{api_results.total}")
    
    all_errors = pure_results.errors + api_results.errors
    if all_errors:
        print("\nAll failures:")
        for err in all_errors:
            print(f"  • {err}")
    
    print("="*70)
    
    # Exit code
    return 0 if len(all_errors) == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
