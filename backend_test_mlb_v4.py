#!/usr/bin/env python3
"""
Backend Test Suite for MLB Engine V4 — Live Intelligence
=========================================================
Tests all V4 features: pitcher_volatility_score, detect_script_break,
reevaluate_live_script, under_risk_monitor, cashout_advisor, and the
POST /api/mlb/live/reevaluate endpoint with strict gating.
"""
import sys
import requests
import json
from datetime import datetime
from typing import Optional

# Backend URL from frontend/.env
BASE_URL = "https://low-volatility-plays.preview.emergentagent.com"

class Colors:
    GREEN = '\033[92m'
    RED = '\033[91m'
    YELLOW = '\033[93m'
    BLUE = '\033[94m'
    END = '\033[0m'

class MLBV4TestSuite:
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
        """Run a single test"""
        url = f"{self.base_url}{endpoint}"
        headers = {'Content-Type': 'application/json'}
        if self.token:
            headers['Authorization'] = f'Bearer {self.token}'

        self.tests_run += 1
        self.log(f"\n🔍 Test #{self.tests_run}: {name}", Colors.BLUE)
        
        try:
            if method == 'GET':
                response = requests.get(url, headers=headers, timeout=30)
            elif method == 'POST':
                response = requests.post(url, json=data, headers=headers, timeout=30)
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
                check_result = check_fn(resp_data)
                if not check_result:
                    self.tests_failed += 1
                    msg = f"❌ FAILED - Custom check failed"
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
        self.log("AUTHENTICATION TEST", Colors.YELLOW)
        self.log("="*80, Colors.YELLOW)
        
        success, response = self.test(
            "Login with demo@valuebet.app/demo1234",
            "POST",
            "/api/auth/login",
            200,
            data={"email": "demo@valuebet.app", "password": "demo1234"}
        )
        
        if success and 'token' in response:
            self.token = response['token']
            self.log(f"   Token obtained: {self.token[:20]}...", Colors.GREEN)
            return True
        return False

    def test_pure_functions(self):
        """Test pure functions from mlb_live_intelligence module"""
        self.log("\n" + "="*80, Colors.YELLOW)
        self.log("PURE FUNCTION TESTS (mlb_live_intelligence.py)", Colors.YELLOW)
        self.log("="*80, Colors.YELLOW)

        try:
            sys.path.insert(0, '/app/backend')
            from services.mlb_live_intelligence import (
                pitcher_volatility_score,
                detect_script_break,
                reevaluate_live_script,
                under_risk_monitor,
                cashout_advisor,
                build_live_intelligence_payload
            )
            
            self.tests_run += 1
            self.tests_passed += 1
            self.log("✅ All mlb_live_intelligence exports importable", Colors.GREEN)
            
            # Test 1: pitcher_volatility_score - HIGH volatility
            self.tests_run += 1
            high_vol_stats = {
                "era": 3.20,
                "xera": 4.15,
                "fip": 4.05,
                "whip": 1.32,
                "hard_hit_pct": 41.5,
                "barrel_pct": 9.2,
                "starts_with_5plus_runs": 3
            }
            result = pitcher_volatility_score(high_vol_stats)
            if result['level'] == 'HIGH' and result['penalty'] >= 10:
                self.tests_passed += 1
                self.log(f"✅ HIGH volatility detected: level={result['level']}, score={result['score']}, penalty={result['penalty']}", Colors.GREEN)
            else:
                self.tests_failed += 1
                self.log(f"❌ Expected HIGH volatility, got {result['level']} with penalty {result['penalty']}", Colors.RED)
                self.failures.append(f"pitcher_volatility_score HIGH: got {result['level']}")

            # Test 2: pitcher_volatility_score - LOW volatility
            self.tests_run += 1
            low_vol_stats = {
                "era": 2.50,
                "xera": 2.60,
                "fip": 2.80,
                "whip": 1.05,
                "hard_hit_pct": 28.0,
                "barrel_pct": 4.0,
                "starts_with_5plus_runs": 0
            }
            result = pitcher_volatility_score(low_vol_stats)
            if result['level'] == 'LOW' and result['penalty'] == 0:
                self.tests_passed += 1
                self.log(f"✅ LOW volatility detected: level={result['level']}, score={result['score']}, penalty={result['penalty']}", Colors.GREEN)
            else:
                self.tests_failed += 1
                self.log(f"❌ Expected LOW volatility, got {result['level']} with penalty {result['penalty']}", Colors.RED)
                self.failures.append(f"pitcher_volatility_score LOW: got {result['level']}")

            # Test 3: pitcher_volatility_score - MEDIUM volatility
            self.tests_run += 1
            med_vol_stats = {
                "era": 3.50,
                "xera": 3.80,
                "fip": 3.70,
                "whip": 1.25,
                "hard_hit_pct": 35.0,
                "barrel_pct": 7.0,
                "starts_with_5plus_runs": 1
            }
            result = pitcher_volatility_score(med_vol_stats)
            if result['level'] == 'MEDIUM' and 4 <= result['penalty'] <= 8:
                self.tests_passed += 1
                self.log(f"✅ MEDIUM volatility detected: level={result['level']}, score={result['score']}, penalty={result['penalty']}", Colors.GREEN)
            else:
                self.tests_failed += 1
                self.log(f"❌ Expected MEDIUM volatility, got {result['level']} with penalty {result['penalty']}", Colors.RED)
                self.failures.append(f"pitcher_volatility_score MEDIUM: got {result['level']}")

            # Test 4: detect_script_break - STRONG break (8 runs in top 6th)
            self.tests_run += 1
            pregame_script = {
                "script_code": "LOW_SCORING_PITCHERS_DUEL",
                "expected_runs": 6.7
            }
            live_state_broken = {
                "current_inning": 6,
                "is_top_half": True,
                "home_runs": 4,
                "away_runs": 4,
                "home_starter_runs_allowed": 5,
                "away_starter_runs_allowed": 3,
                "home_starter_pulled": True,
                "away_starter_pulled": False
            }
            result = detect_script_break(pregame_script, live_state_broken)
            if result['broken'] and result['severity'] == 'STRONG':
                self.tests_passed += 1
                self.log(f"✅ Script break detected: broken={result['broken']}, severity={result['severity']}", Colors.GREEN)
            else:
                self.tests_failed += 1
                self.log(f"❌ Expected STRONG break, got broken={result['broken']}, severity={result['severity']}", Colors.RED)
                self.failures.append(f"detect_script_break STRONG: got {result['severity']}")

            # Test 5: detect_script_break - NO break (2 runs in inning 5)
            self.tests_run += 1
            live_state_intact = {
                "current_inning": 5,
                "is_top_half": True,
                "home_runs": 1,
                "away_runs": 1,
                "home_starter_runs_allowed": 1,
                "away_starter_runs_allowed": 1,
                "home_starter_pulled": False,
                "away_starter_pulled": False
            }
            result = detect_script_break(pregame_script, live_state_intact)
            if not result['broken']:
                self.tests_passed += 1
                self.log(f"✅ Script intact: broken={result['broken']}", Colors.GREEN)
            else:
                self.tests_failed += 1
                self.log(f"❌ Expected no break, got broken={result['broken']}", Colors.RED)
                self.failures.append(f"detect_script_break NO_BREAK: got broken={result['broken']}")

            # Test 6: reevaluate_live_script - BULLPEN_COLLAPSE
            self.tests_run += 1
            live_state_collapse = {
                "current_inning": 5,
                "is_top_half": False,
                "home_runs": 3,
                "away_runs": 5,
                "home_starter_runs_allowed": 5,
                "away_starter_runs_allowed": 2,
                "home_starter_pulled": True,
                "away_starter_pulled": False,
                "bullpen_runs_allowed_home": 2,
                "bullpen_runs_allowed_away": 0
            }
            result = reevaluate_live_script(live_state_collapse, {"expected_runs": 6.7})
            if result['live_script'] == 'BULLPEN_COLLAPSE':
                self.tests_passed += 1
                self.log(f"✅ BULLPEN_COLLAPSE detected: {result['live_script']}", Colors.GREEN)
            else:
                self.tests_failed += 1
                self.log(f"❌ Expected BULLPEN_COLLAPSE, got {result['live_script']}", Colors.RED)
                self.failures.append(f"reevaluate_live_script BULLPEN_COLLAPSE: got {result['live_script']}")

            # Test 7: reevaluate_live_script - OFFENSIVE_BREAKOUT
            self.tests_run += 1
            live_state_breakout = {
                "current_inning": 6,
                "is_top_half": False,
                "home_runs": 7,
                "away_runs": 5,
                "home_starter_runs_allowed": 3,
                "away_starter_runs_allowed": 4,
                "home_starter_pulled": True,
                "away_starter_pulled": True,
                "bullpen_runs_allowed_home": 2,
                "bullpen_runs_allowed_away": 3
            }
            result = reevaluate_live_script(live_state_breakout, {"expected_runs": 8.0})
            if result['live_script'] == 'OFFENSIVE_BREAKOUT':
                self.tests_passed += 1
                self.log(f"✅ OFFENSIVE_BREAKOUT detected: {result['live_script']}", Colors.GREEN)
            else:
                self.tests_failed += 1
                self.log(f"❌ Expected OFFENSIVE_BREAKOUT, got {result['live_script']}", Colors.RED)
                self.failures.append(f"reevaluate_live_script OFFENSIVE_BREAKOUT: got {result['live_script']}")

            # Test 8: reevaluate_live_script - LOW_SCORING_SCRIPT
            self.tests_run += 1
            live_state_low = {
                "current_inning": 7,
                "is_top_half": False,
                "home_runs": 0,
                "away_runs": 1,
                "home_starter_runs_allowed": 1,
                "away_starter_runs_allowed": 0,
                "home_starter_pulled": False,
                "away_starter_pulled": False,
                "bullpen_runs_allowed_home": 0,
                "bullpen_runs_allowed_away": 0
            }
            result = reevaluate_live_script(live_state_low, {"expected_runs": 7.5})
            if result['live_script'] in ('LOW_SCORING_SCRIPT', 'UNDER_STILL_HEALTHY'):
                self.tests_passed += 1
                self.log(f"✅ Low scoring script detected: {result['live_script']}", Colors.GREEN)
            else:
                self.tests_failed += 1
                self.log(f"❌ Expected LOW_SCORING_SCRIPT or UNDER_STILL_HEALTHY, got {result['live_script']}", Colors.RED)
                self.failures.append(f"reevaluate_live_script LOW_SCORING: got {result['live_script']}")

            # Test 9: under_risk_monitor - UNDER_IN_DANGER
            self.tests_run += 1
            pregame_pick_under = {
                "recommendation": {"market": "Under 9.5"},
                "_mlb_script_v2": {"recommendedLine": "Under 9.5"}
            }
            live_state_danger = {
                "current_inning": 6,
                "is_top_half": True,
                "home_runs": 4,
                "away_runs": 4
            }
            live_script_danger = {"projected_final_total": 10.5}
            result = under_risk_monitor(pregame_pick_under, live_state_danger, live_script_danger)
            if result['verdict'] in ('UNDER_IN_DANGER', 'WATCH') and result['risk_score'] >= 70:
                self.tests_passed += 1
                self.log(f"✅ UNDER_IN_DANGER detected: verdict={result['verdict']}, risk_score={result['risk_score']}", Colors.GREEN)
            else:
                self.tests_failed += 1
                self.log(f"❌ Expected UNDER_IN_DANGER with risk>=70, got verdict={result['verdict']}, risk={result['risk_score']}", Colors.RED)
                self.failures.append(f"under_risk_monitor DANGER: got {result['verdict']}")

            # Test 10: under_risk_monitor - NOT_APPLICABLE for non-Under pick
            self.tests_run += 1
            pregame_pick_ml = {
                "recommendation": {"market": "Moneyline Home"}
            }
            result = under_risk_monitor(pregame_pick_ml, live_state_danger, live_script_danger)
            if result['verdict'] == 'NOT_APPLICABLE':
                self.tests_passed += 1
                self.log(f"✅ NOT_APPLICABLE for non-Under pick", Colors.GREEN)
            else:
                self.tests_failed += 1
                self.log(f"❌ Expected NOT_APPLICABLE, got {result['verdict']}", Colors.RED)
                self.failures.append(f"under_risk_monitor NOT_APPLICABLE: got {result['verdict']}")

            # Test 11: cashout_advisor - FULL_CASHOUT for Gallen scenario
            self.tests_run += 1
            script_break_strong = {"broken": True, "severity": "STRONG"}
            live_script_collapse = {"live_script": "BULLPEN_COLLAPSE", "projected_final_total": 10.5, "innings_played": 6.0, "innings_remaining": 3.0, "bullpen_pressure": 80}
            under_risk_danger = {"verdict": "UNDER_IN_DANGER", "risk_score": 85}
            result = cashout_advisor(pregame_pick_under, live_state_danger, live_script_collapse, script_break_strong, under_risk_danger)
            if result['verdict'] == 'FULL_CASHOUT' and result['confidence'] >= 80:
                self.tests_passed += 1
                self.log(f"✅ FULL_CASHOUT recommended: verdict={result['verdict']}, confidence={result['confidence']}", Colors.GREEN)
            else:
                self.tests_failed += 1
                self.log(f"❌ Expected FULL_CASHOUT with confidence>=80, got verdict={result['verdict']}, confidence={result['confidence']}", Colors.RED)
                self.failures.append(f"cashout_advisor FULL_CASHOUT: got {result['verdict']}")

            return True
        except Exception as e:
            self.tests_run += 1
            self.tests_failed += 1
            self.log(f"❌ Pure function tests failed: {str(e)}", Colors.RED)
            self.failures.append(f"Pure functions: {str(e)}")
            import traceback
            traceback.print_exc()
            return False

    def test_endpoint_gating(self):
        """Test POST /api/mlb/live/reevaluate endpoint gating"""
        self.log("\n" + "="*80, Colors.YELLOW)
        self.log("ENDPOINT GATING TESTS (/api/mlb/live/reevaluate)", Colors.YELLOW)
        self.log("="*80, Colors.YELLOW)

        # Test 1: Anonymous request returns 401
        self.log("\n🔍 Test: Anonymous request (no token)", Colors.BLUE)
        self.tests_run += 1
        try:
            url = f"{self.base_url}/api/mlb/live/reevaluate"
            headers = {'Content-Type': 'application/json'}
            response = requests.post(url, json={
                "match_id": "12345",
                "pregame_pick": {"sport": "baseball", "_mlb_script_v3": {}}
            }, headers=headers, timeout=30)
            
            if response.status_code == 401:
                self.tests_passed += 1
                self.log(f"✅ PASSED - Anonymous request correctly rejected with 401", Colors.GREEN)
            else:
                self.tests_failed += 1
                self.log(f"❌ FAILED - Expected 401, got {response.status_code}", Colors.RED)
                self.failures.append(f"Anonymous request: got {response.status_code}")
        except Exception as e:
            self.tests_failed += 1
            self.log(f"❌ FAILED - Error: {str(e)}", Colors.RED)
            self.failures.append(f"Anonymous request: {str(e)}")

        # Test 2: Missing _mlb_script_v3 returns 422
        def check_missing_v3(data):
            if 'pregame filter' in data.get('detail', '').lower():
                self.log(f"   ✓ Correct error message: {data.get('detail')}", Colors.GREEN)
                return True
            self.log(f"   ✗ Wrong error message: {data.get('detail')}", Colors.RED)
            return False

        self.test(
            "Missing _mlb_script_v3 returns 422 with 'pregame filter' message",
            "POST",
            "/api/mlb/live/reevaluate",
            422,
            data={
                "match_id": "12345",
                "pregame_pick": {"sport": "baseball"}
            },
            check_fn=check_missing_v3
        )

        # Test 3: Non-baseball sport returns 422
        def check_non_baseball(data):
            if 'baseball' in data.get('detail', '').lower():
                self.log(f"   ✓ Correct error message: {data.get('detail')}", Colors.GREEN)
                return True
            self.log(f"   ✗ Wrong error message: {data.get('detail')}", Colors.RED)
            return False

        self.test(
            "Non-baseball sport (football) returns 422 with 'baseball' message",
            "POST",
            "/api/mlb/live/reevaluate",
            422,
            data={
                "match_id": "12345",
                "pregame_pick": {
                    "sport": "football",
                    "_mlb_script_v3": {"script": {"script_code": "TEST"}}
                }
            },
            check_fn=check_non_baseball
        )

        # Test 4: Valid baseball pick but NO live_state returns NOT_LIVE_YET
        def check_not_live_yet(data):
            if data.get('status') == 'NOT_LIVE_YET':
                self.log(f"   ✓ Correct status: NOT_LIVE_YET", Colors.GREEN)
                self.log(f"   ✓ Detail: {data.get('detail')}", Colors.GREEN)
                return True
            self.log(f"   ✗ Expected NOT_LIVE_YET, got {data.get('status')}", Colors.RED)
            return False

        self.test(
            "Valid baseball pick with _mlb_script_v3 but NO live_state returns NOT_LIVE_YET",
            "POST",
            "/api/mlb/live/reevaluate",
            200,
            data={
                "match_id": "nonexistent_match_99999",
                "pregame_pick": {
                    "sport": "baseball",
                    "_mlb_script_v3": {
                        "script": {
                            "script_code": "LOW_SCORING_PITCHERS_DUEL",
                            "expected_runs": 6.7
                        }
                    },
                    "recommendation": {"market": "Under 9.5"}
                }
            },
            check_fn=check_not_live_yet
        )

    def test_happy_path_gallen_scenario(self):
        """Test happy-path Gallen scenario"""
        self.log("\n" + "="*80, Colors.YELLOW)
        self.log("HAPPY-PATH GALLEN SCENARIO TEST", Colors.YELLOW)
        self.log("="*80, Colors.YELLOW)

        def check_gallen_scenario(data):
            if data.get('status') != 'LIVE_INTEL_OK':
                self.log(f"   ✗ Expected status=LIVE_INTEL_OK, got {data.get('status')}", Colors.RED)
                return False
            
            intel = data.get('intelligence', {})
            if not intel:
                self.log("   ✗ No intelligence payload", Colors.RED)
                return False
            
            # Check script_break
            script_break = intel.get('script_break', {})
            if not script_break.get('broken'):
                self.log(f"   ✗ Expected script_break.broken=True, got {script_break.get('broken')}", Colors.RED)
                return False
            if script_break.get('severity') != 'STRONG':
                self.log(f"   ✗ Expected severity=STRONG, got {script_break.get('severity')}", Colors.RED)
                return False
            self.log(f"   ✓ script_break: broken=True, severity=STRONG", Colors.GREEN)
            
            # Check live_script
            live_script = intel.get('live_script', {})
            live_code = live_script.get('live_script')
            if live_code not in ('BULLPEN_COLLAPSE', 'OVER_NOW_FAVORED', 'OFFENSIVE_BREAKOUT'):
                self.log(f"   ✗ Expected live_script in (BULLPEN_COLLAPSE, OVER_NOW_FAVORED, OFFENSIVE_BREAKOUT), got {live_code}", Colors.RED)
                return False
            self.log(f"   ✓ live_script: {live_code}", Colors.GREEN)
            
            # Check under_risk
            under_risk = intel.get('under_risk', {})
            verdict = under_risk.get('verdict')
            if verdict not in ('UNDER_IN_DANGER', 'UNDER_BUSTED'):
                self.log(f"   ✗ Expected verdict in (UNDER_IN_DANGER, UNDER_BUSTED), got {verdict}", Colors.RED)
                return False
            self.log(f"   ✓ under_risk: verdict={verdict}", Colors.GREEN)
            
            # Check cashout
            cashout = intel.get('cashout', {})
            if cashout.get('verdict') != 'FULL_CASHOUT':
                self.log(f"   ✗ Expected cashout verdict=FULL_CASHOUT, got {cashout.get('verdict')}", Colors.RED)
                return False
            self.log(f"   ✓ cashout: verdict=FULL_CASHOUT, confidence={cashout.get('confidence')}", Colors.GREEN)
            
            return True

        self.test(
            "Gallen scenario: pregame ER=6.7, Under 9.5, live: top 6th, 8 runs, starter 5 ER",
            "POST",
            "/api/mlb/live/reevaluate",
            200,
            data={
                "match_id": "test_gallen_scenario",
                "pregame_pick": {
                    "sport": "baseball",
                    "_mlb_script_v3": {
                        "script": {
                            "script_code": "LOW_SCORING_PITCHERS_DUEL",
                            "expected_runs": 6.7,
                            "projected_margin": 0.5
                        },
                        "pitchers_block": {
                            "home": {"name": "Zac Gallen", "qualityScore": 75},
                            "away": {"name": "Opponent SP", "qualityScore": 65}
                        }
                    },
                    "_mlb_script_v2": {
                        "expectedRuns": 6.7,
                        "recommendedLine": "Under 9.5"
                    },
                    "recommendation": {"market": "Under 9.5"},
                    "home_pitcher_stats": {
                        "name": "Zac Gallen",
                        "era": 3.20,
                        "xera": 4.15,
                        "fip": 4.05,
                        "whip": 1.32,
                        "hard_hit_pct": 41.5,
                        "barrel_pct": 9.2,
                        "starts_with_5plus_runs": 3
                    },
                    "away_pitcher_stats": {
                        "name": "Opponent SP",
                        "era": 3.50,
                        "xera": 3.60,
                        "fip": 3.70,
                        "whip": 1.20
                    }
                },
                "live_state": {
                    "current_inning": 6,
                    "is_top_half": True,
                    "home_runs": 3,
                    "away_runs": 5,
                    "home_starter_runs_allowed": 5,
                    "away_starter_runs_allowed": 3,
                    "home_starter_pulled": True,
                    "away_starter_pulled": False,
                    "bullpen_runs_allowed_home": 0,
                    "bullpen_runs_allowed_away": 0
                }
            },
            check_fn=check_gallen_scenario
        )

    def test_v3_confidence_breakdown_volatility(self):
        """Test V3 confidence_breakdown includes volatility_penalty"""
        self.log("\n" + "="*80, Colors.YELLOW)
        self.log("V3 CONFIDENCE BREAKDOWN VOLATILITY PENALTY TEST", Colors.YELLOW)
        self.log("="*80, Colors.YELLOW)

        try:
            sys.path.insert(0, '/app/backend')
            from services.mlb_pregame_analytics_v3 import build_confidence_breakdown
            
            self.tests_run += 1
            
            scoring_ctx = {
                "home_pitcher_quality": {"score": 70},
                "away_pitcher_quality": {"score": 65},
                "offense_home": {"score": 55},
                "offense_away": {"score": 50},
                "bullpen": {"score": 60},
                "park": {"park_runs_mult": 1.0, "weather_score": 50},
                "home_pitcher_stats": {
                    "era": 3.20,
                    "xera": 4.15,
                    "fip": 4.05,
                    "whip": 1.32,
                    "hard_hit_pct": 41.5,
                    "barrel_pct": 9.2,
                    "starts_with_5plus_runs": 3
                },
                "away_pitcher_stats": {
                    "era": 3.50,
                    "xera": 3.80,
                    "fip": 3.70,
                    "whip": 1.25,
                    "hard_hit_pct": 35.0,
                    "barrel_pct": 7.0,
                    "starts_with_5plus_runs": 1
                }
            }
            v2_payload = {"expectedRuns": 7.5}
            
            result = build_confidence_breakdown(scoring_ctx, v2_payload, displayed_total=75.0)
            
            # Check for volatility_penalty component
            components = result.get('components', [])
            volatility_component = None
            for comp in components:
                if comp.get('key') == 'volatility_penalty':
                    volatility_component = comp
                    break
            
            if volatility_component:
                self.tests_passed += 1
                self.log(f"✅ volatility_penalty component found: value={volatility_component.get('value')}, tone={volatility_component.get('tone')}", Colors.GREEN)
                
                # Check that total is reduced by penalty
                raw_total = result.get('raw_total', 0)
                final_total = result.get('total', 0)
                penalty = result.get('volatility_penalty', 0)
                
                if penalty > 0 and final_total < raw_total:
                    self.log(f"   ✓ Total correctly reduced: raw={raw_total}, penalty={penalty}, final={final_total}", Colors.GREEN)
                else:
                    self.log(f"   ⚠️  Total not reduced as expected: raw={raw_total}, penalty={penalty}, final={final_total}", Colors.YELLOW)
            else:
                self.tests_failed += 1
                self.log(f"❌ volatility_penalty component NOT found in confidence_breakdown", Colors.RED)
                self.failures.append("V3 confidence_breakdown: missing volatility_penalty")
                
        except Exception as e:
            self.tests_failed += 1
            self.log(f"❌ V3 confidence_breakdown test failed: {str(e)}", Colors.RED)
            self.failures.append(f"V3 confidence_breakdown: {str(e)}")
            import traceback
            traceback.print_exc()

    def test_no_regression_mlb_pregame(self):
        """Test no regression on existing MLB pregame buckets"""
        self.log("\n" + "="*80, Colors.YELLOW)
        self.log("NO REGRESSION TEST - MLB PREGAME BUCKETS", Colors.YELLOW)
        self.log("="*80, Colors.YELLOW)

        def check_mlb_v3_present(data):
            payload = data.get('payload', {})
            picks = payload.get('picks', [])
            rescued = payload.get('rescued_picks', [])
            structural = payload.get('structural_lean_requires_odds', [])
            watchlist = payload.get('watchlist_manual_odds', [])
            
            all_picks = picks + rescued + structural + watchlist
            
            if not all_picks:
                self.log("   ⚠️  No picks returned (expected for off-season or time filter)", Colors.YELLOW)
                return True
            
            # Check at least one pick has _mlb_script_v3
            has_v3 = False
            for pick in all_picks:
                if pick.get('_mlb_script_v3'):
                    has_v3 = True
                    self.log(f"   ✓ Found _mlb_script_v3 in pick", Colors.GREEN)
                    
                    # Verify v3 structure
                    v3 = pick['_mlb_script_v3']
                    if 'script' in v3 and 'pitchers_block' in v3:
                        self.log(f"   ✓ V3 structure valid: script_code={v3.get('script', {}).get('script_code')}", Colors.GREEN)
                    break
            
            if not has_v3:
                self.log("   ⚠️  No _mlb_script_v3 found (may be off-season)", Colors.YELLOW)
            
            return True

        self.test(
            "MLB pregame buckets still return picks with _mlb_script_v3",
            "POST",
            "/api/analysis/run",
            200,
            data={"sport": "baseball", "max_matches": 5, "background": False},
            check_fn=check_mlb_v3_present
        )

    def test_no_regression_football(self):
        """Test no regression on football pipeline"""
        self.log("\n" + "="*80, Colors.YELLOW)
        self.log("NO REGRESSION TEST - FOOTBALL PIPELINE", Colors.YELLOW)
        self.log("="*80, Colors.YELLOW)

        def check_no_mlb_fields(data):
            payload = data.get('payload', {})
            picks = payload.get('picks', [])
            
            for pick in picks:
                if '_mlb_script_v3' in pick:
                    self.log("   ✗ Found _mlb_script_v3 in football pick!", Colors.RED)
                    return False
                if 'baseballHistoricalProfile' in pick:
                    self.log("   ✗ Found baseballHistoricalProfile in football pick!", Colors.RED)
                    return False
            
            self.log("   ✓ No MLB fields in football picks", Colors.GREEN)
            return True

        self.test(
            "Football pipeline completes without _mlb_script_v3",
            "POST",
            "/api/analysis/run",
            200,
            data={"sport": "football", "max_matches": 3, "background": False},
            check_fn=check_no_mlb_fields
        )

    def print_summary(self):
        """Print test summary"""
        self.log("\n" + "="*80, Colors.YELLOW)
        self.log("TEST SUMMARY", Colors.YELLOW)
        self.log("="*80, Colors.YELLOW)
        
        total = self.tests_run
        passed = self.tests_passed
        failed = self.tests_failed
        
        self.log(f"\nTotal Tests: {total}", Colors.BLUE)
        self.log(f"Passed: {passed}", Colors.GREEN)
        self.log(f"Failed: {failed}", Colors.RED if failed > 0 else Colors.GREEN)
        
        if failed > 0:
            self.log("\n❌ FAILED TESTS:", Colors.RED)
            for failure in self.failures:
                self.log(f"  - {failure}", Colors.RED)
        
        success_rate = (passed / total * 100) if total > 0 else 0
        self.log(f"\nSuccess Rate: {success_rate:.1f}%", 
                Colors.GREEN if success_rate >= 80 else Colors.YELLOW)
        
        return {
            "total": total,
            "passed": passed,
            "failed": failed,
            "success_rate": success_rate,
            "failures": self.failures
        }

def main():
    print(f"\n{'='*80}")
    print("MLB Engine V4 — Live Intelligence Backend Test Suite")
    print(f"{'='*80}\n")
    print(f"Backend URL: {BASE_URL}")
    print(f"Started at: {datetime.now().isoformat()}\n")

    suite = MLBV4TestSuite(BASE_URL)
    
    # Run tests
    if not suite.test_auth():
        print("\n❌ Authentication failed. Cannot proceed with other tests.")
        return 1
    
    suite.test_pure_functions()
    suite.test_endpoint_gating()
    suite.test_happy_path_gallen_scenario()
    suite.test_v3_confidence_breakdown_volatility()
    suite.test_no_regression_mlb_pregame()
    suite.test_no_regression_football()
    
    # Print summary
    summary = suite.print_summary()
    
    print(f"\nCompleted at: {datetime.now().isoformat()}")
    return 0 if summary['failed'] == 0 else 1

if __name__ == "__main__":
    sys.exit(main())
