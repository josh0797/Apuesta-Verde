#!/usr/bin/env python3
"""
Backend test suite for Live Territorial Control + Corner Intelligence.

Tests:
  1. Pure functions: classify_territorial_state() with 3 canonical cases
  2. Pure functions: evaluate_live_territorial_control()
  3. Pure functions: evaluate_corner_pressure()
  4. Pure functions: rank_live_markets() CRITICAL RULE enforcement
  5. API: POST /api/football/live/territorial_control (happy path, validation, persistence)
  6. API: GET /api/football/live/territorial_control/history
"""
import sys
import os
import requests
from datetime import datetime

# Get backend URL from environment
BACKEND_URL = os.environ.get("REACT_APP_BACKEND_URL", "https://low-volatility-plays.preview.emergentagent.com")
API_BASE = f"{BACKEND_URL}/api"

# Test credentials
TEST_EMAIL = "demo@valuebet.app"
TEST_PASSWORD = "demo1234"


class LiveTerritorialControlTester:
    def __init__(self):
        self.token = None
        self.tests_run = 0
        self.tests_passed = 0
        self.tests_failed = 0
        self.failures = []

    def log(self, msg, level="INFO"):
        prefix = {
            "INFO": "ℹ️ ",
            "PASS": "✅",
            "FAIL": "❌",
            "WARN": "⚠️ ",
        }.get(level, "  ")
        print(f"{prefix} {msg}")

    def assert_equal(self, actual, expected, test_name):
        """Assert equality and track results."""
        self.tests_run += 1
        if actual == expected:
            self.tests_passed += 1
            self.log(f"PASS: {test_name}", "PASS")
            return True
        else:
            self.tests_failed += 1
            msg = f"FAIL: {test_name} | Expected: {expected}, Got: {actual}"
            self.log(msg, "FAIL")
            self.failures.append(msg)
            return False

    def assert_true(self, condition, test_name):
        """Assert condition is true."""
        self.tests_run += 1
        if condition:
            self.tests_passed += 1
            self.log(f"PASS: {test_name}", "PASS")
            return True
        else:
            self.tests_failed += 1
            msg = f"FAIL: {test_name} | Condition was False"
            self.log(msg, "FAIL")
            self.failures.append(msg)
            return False

    def assert_in(self, item, container, test_name):
        """Assert item is in container."""
        self.tests_run += 1
        if item in container:
            self.tests_passed += 1
            self.log(f"PASS: {test_name}", "PASS")
            return True
        else:
            self.tests_failed += 1
            msg = f"FAIL: {test_name} | {item} not in {container}"
            self.log(msg, "FAIL")
            self.failures.append(msg)
            return False

    def assert_not_in(self, item, container, test_name):
        """Assert item is NOT in container."""
        self.tests_run += 1
        if item not in container:
            self.tests_passed += 1
            self.log(f"PASS: {test_name}", "PASS")
            return True
        else:
            self.tests_failed += 1
            msg = f"FAIL: {test_name} | {item} should NOT be in {container}"
            self.log(msg, "FAIL")
            self.failures.append(msg)
            return False

    def assert_gte(self, actual, expected, test_name):
        """Assert actual >= expected."""
        self.tests_run += 1
        if actual >= expected:
            self.tests_passed += 1
            self.log(f"PASS: {test_name}", "PASS")
            return True
        else:
            self.tests_failed += 1
            msg = f"FAIL: {test_name} | {actual} < {expected}"
            self.log(msg, "FAIL")
            self.failures.append(msg)
            return False

    def login(self):
        """Authenticate and get token."""
        self.log(f"Logging in as {TEST_EMAIL}...")
        try:
            resp = requests.post(
                f"{API_BASE}/auth/login",
                json={"email": TEST_EMAIL, "password": TEST_PASSWORD},
                timeout=10,
            )
            if resp.status_code == 200:
                data = resp.json()
                self.token = data.get("token")
                if self.token:
                    self.log("Login successful", "PASS")
                    return True
                else:
                    self.log("Login response missing token", "FAIL")
                    return False
            else:
                self.log(f"Login failed: {resp.status_code} {resp.text}", "FAIL")
                return False
        except Exception as e:
            self.log(f"Login exception: {e}", "FAIL")
            return False

    def headers(self):
        """Return auth headers."""
        return {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
        }

    # ═══════════════════════════════════════════════════════════════════════
    # CANONICAL TEST CASES
    # ═══════════════════════════════════════════════════════════════════════

    def get_psg_canonical_metrics(self):
        """PSG 0-1 Arsenal, min 29 → CORNER_PRESSURE_STATE."""
        return {
            "minute": 29,
            "score_home": 0,
            "score_away": 1,
            "home_team": "PSG",
            "away_team": "Arsenal",
            "possession_home": 73,
            "possession_away": 27,
            "xt_home": 32,
            "xt_away": 12,
            "xg_home": 0.10,
            "xg_away": 0.32,
            "shots_home": 1,
            "shots_away": 1,
            "shots_on_target_home": 0,
            "shots_on_target_away": 1,
            "corners_home": 2,
            "corners_away": 0,
            "dangerous_attacks_home": 7,
            "dangerous_attacks_away": 3,
        }

    def get_control_with_pressure_metrics(self):
        """Control with pressure → CONTROL_WITH_PRESSURE + strong_conversion."""
        return {
            "minute": 35,
            "score_home": 0,
            "score_away": 0,
            "home_team": "Team A",
            "away_team": "Team B",
            "possession_home": 72,
            "possession_away": 28,
            "xt_home": 40,
            "xt_away": 8,
            "xg_home": 0.85,
            "xg_away": 0.15,
            "shots_home": 6,
            "shots_away": 1,
            "shots_on_target_home": 3,
            "shots_on_target_away": 0,
            "corners_home": 4,
            "corners_away": 0,
            "dangerous_attacks_home": 15,
            "dangerous_attacks_away": 2,
        }

    def get_no_clear_dominance_metrics(self):
        """Genuine NO_CLEAR_DOMINANCE."""
        return {
            "minute": 25,
            "score_home": 0,
            "score_away": 0,
            "home_team": "Team X",
            "away_team": "Team Y",
            "possession_home": 52,
            "possession_away": 48,
            "xt_home": 14,
            "xt_away": 11,
            "xg_home": 0.30,
            "xg_away": 0.28,
            "shots_home": 2,
            "shots_away": 2,
            "shots_on_target_home": 1,
            "shots_on_target_away": 1,
            "corners_home": 1,
            "corners_away": 1,
            "dangerous_attacks_home": 3,
            "dangerous_attacks_away": 3,
        }

    # ═══════════════════════════════════════════════════════════════════════
    # TEST 1: POST /api/football/live/territorial_control — PSG CANONICAL
    # ═══════════════════════════════════════════════════════════════════════

    def test_psg_canonical_case(self):
        """
        Test PSG canonical case via API:
        - state == CORNER_PRESSURE_STATE
        - corner_pressure_state == True
        - controlling_team == PSG
        - losing_team == PSG
        - strong_conversion == False
        - corner score >= 55
        - ranked_markets[0] must be a corners market (NOT Over 2.5 / BTTS / Next Goal)
        """
        self.log("\n" + "="*70)
        self.log("TEST 1: PSG Canonical Case (CORNER_PRESSURE_STATE)")
        self.log("="*70)

        metrics = self.get_psg_canonical_metrics()
        try:
            resp = requests.post(
                f"{API_BASE}/football/live/territorial_control",
                json={"metrics": metrics, "surface_threshold": 55},
                headers=self.headers(),
                timeout=15,
            )
            self.log(f"Response status: {resp.status_code}")
            
            if resp.status_code != 200:
                self.log(f"Response body: {resp.text}", "FAIL")
                self.assert_true(False, "PSG case API call should return 200")
                return

            data = resp.json()
            self.assert_true(data.get("ok") is True, "PSG case: ok=True")
            
            territorial = data.get("territorial", {})
            corner = data.get("corner", {})
            ranked = data.get("ranked_markets", [])
            
            # Check territorial state
            self.assert_equal(
                territorial.get("state"),
                "CORNER_PRESSURE_STATE",
                "PSG case: state == CORNER_PRESSURE_STATE"
            )
            self.assert_true(
                territorial.get("corner_pressure_state") is True,
                "PSG case: corner_pressure_state == True"
            )
            self.assert_equal(
                territorial.get("controlling_team"),
                "PSG",
                "PSG case: controlling_team == PSG"
            )
            self.assert_equal(
                territorial.get("losing_team"),
                "PSG",
                "PSG case: losing_team == PSG"
            )
            self.assert_true(
                territorial.get("strong_conversion") is False,
                "PSG case: strong_conversion == False"
            )
            
            # Check corner engine
            corner_score = corner.get("score", 0)
            self.assert_gte(
                corner_score,
                55,
                f"PSG case: corner score >= 55 (got {corner_score})"
            )
            self.assert_true(
                corner.get("surface_recommendation") is True,
                "PSG case: corner surface_recommendation == True"
            )
            
            # CRITICAL: Check ranked markets — first market MUST be corners, NOT goals
            if ranked:
                top_market = ranked[0]
                top_category = top_market.get("category")
                self.log(f"Top ranked market: {top_market.get('market')} (category: {top_category})")
                
                corners_categories = ["OVER_TEAM_CORNERS", "TEAM_MOST_CORNERS", "NEXT_CORNER"]
                goal_categories = ["NEXT_GOAL", "OVER_GOALS", "BTTS"]
                
                self.assert_in(
                    top_category,
                    corners_categories,
                    f"PSG case: top market category should be corners (got {top_category})"
                )
                
                # Verify NO goal markets in the list when state is CORNER_PRESSURE_STATE
                # and strong_conversion is False
                goal_markets_found = [m for m in ranked if m.get("category") in goal_categories]
                self.assert_equal(
                    len(goal_markets_found),
                    0,
                    "PSG case: NO goal markets should be present (TERRITORIAL_CONTROL rule)"
                )
            else:
                self.assert_true(False, "PSG case: ranked_markets should not be empty")
            
            # Check persistence
            eval_id = data.get("evaluation_id")
            self.assert_true(
                eval_id is not None and len(str(eval_id)) > 0,
                f"PSG case: evaluation_id should be present (got {eval_id})"
            )
            
        except Exception as e:
            self.log(f"Exception in PSG canonical test: {e}", "FAIL")
            self.assert_true(False, f"PSG case: Exception {e}")

    # ═══════════════════════════════════════════════════════════════════════
    # TEST 2: CONTROL_WITH_PRESSURE case
    # ═══════════════════════════════════════════════════════════════════════

    def test_control_with_pressure_case(self):
        """
        Test Control with Pressure case:
        - state == CONTROL_WITH_PRESSURE
        - strong_conversion == True
        - ranked_markets should include NEXT_GOAL and possibly OVER_GOALS
        """
        self.log("\n" + "="*70)
        self.log("TEST 2: Control with Pressure Case")
        self.log("="*70)

        metrics = self.get_control_with_pressure_metrics()
        try:
            resp = requests.post(
                f"{API_BASE}/football/live/territorial_control",
                json={"metrics": metrics, "surface_threshold": 55},
                headers=self.headers(),
                timeout=15,
            )
            self.log(f"Response status: {resp.status_code}")
            
            if resp.status_code != 200:
                self.log(f"Response body: {resp.text}", "FAIL")
                self.assert_true(False, "Control with pressure API call should return 200")
                return

            data = resp.json()
            territorial = data.get("territorial", {})
            ranked = data.get("ranked_markets", [])
            
            self.assert_equal(
                territorial.get("state"),
                "CONTROL_WITH_PRESSURE",
                "Control case: state == CONTROL_WITH_PRESSURE"
            )
            self.assert_true(
                territorial.get("strong_conversion") is True,
                "Control case: strong_conversion == True"
            )
            
            # Check that goal markets ARE present
            categories = [m.get("category") for m in ranked]
            self.log(f"Ranked market categories: {categories}")
            
            self.assert_in(
                "NEXT_GOAL",
                categories,
                "Control case: NEXT_GOAL should be in ranked markets"
            )
            
        except Exception as e:
            self.log(f"Exception in control with pressure test: {e}", "FAIL")
            self.assert_true(False, f"Control case: Exception {e}")

    # ═══════════════════════════════════════════════════════════════════════
    # TEST 3: NO_CLEAR_DOMINANCE case
    # ═══════════════════════════════════════════════════════════════════════

    def test_no_clear_dominance_case(self):
        """
        Test genuine NO_CLEAR_DOMINANCE:
        - state == NO_CLEAR_DOMINANCE
        """
        self.log("\n" + "="*70)
        self.log("TEST 3: NO_CLEAR_DOMINANCE Case")
        self.log("="*70)

        metrics = self.get_no_clear_dominance_metrics()
        try:
            resp = requests.post(
                f"{API_BASE}/football/live/territorial_control",
                json={"metrics": metrics, "surface_threshold": 55},
                headers=self.headers(),
                timeout=15,
            )
            self.log(f"Response status: {resp.status_code}")
            
            if resp.status_code != 200:
                self.log(f"Response body: {resp.text}", "FAIL")
                self.assert_true(False, "NO_CLEAR_DOMINANCE API call should return 200")
                return

            data = resp.json()
            territorial = data.get("territorial", {})
            
            self.assert_equal(
                territorial.get("state"),
                "NO_CLEAR_DOMINANCE",
                "NCD case: state == NO_CLEAR_DOMINANCE"
            )
            
        except Exception as e:
            self.log(f"Exception in NO_CLEAR_DOMINANCE test: {e}", "FAIL")
            self.assert_true(False, f"NCD case: Exception {e}")

    # ═══════════════════════════════════════════════════════════════════════
    # TEST 4: API Validation
    # ═══════════════════════════════════════════════════════════════════════

    def test_api_validation(self):
        """
        Test API validation:
        - No match_id and no metrics → 400
        - match_id not found and no metrics → 404
        """
        self.log("\n" + "="*70)
        self.log("TEST 4: API Validation")
        self.log("="*70)

        # Test 1: No match_id and no metrics
        try:
            resp = requests.post(
                f"{API_BASE}/football/live/territorial_control",
                json={},
                headers=self.headers(),
                timeout=10,
            )
            self.assert_equal(
                resp.status_code,
                400,
                "Validation: no match_id and no metrics should return 400"
            )
        except Exception as e:
            self.log(f"Exception in validation test 1: {e}", "FAIL")
            self.assert_true(False, f"Validation test 1: Exception {e}")

        # Test 2: match_id not found and no metrics
        try:
            resp = requests.post(
                f"{API_BASE}/football/live/territorial_control",
                json={"match_id": "nonexistent_match_99999"},
                headers=self.headers(),
                timeout=10,
            )
            self.assert_equal(
                resp.status_code,
                404,
                "Validation: match_id not found and no metrics should return 404"
            )
        except Exception as e:
            self.log(f"Exception in validation test 2: {e}", "FAIL")
            self.assert_true(False, f"Validation test 2: Exception {e}")

    # ═══════════════════════════════════════════════════════════════════════
    # TEST 5: GET /api/football/live/territorial_control/history
    # ═══════════════════════════════════════════════════════════════════════

    def test_history_endpoint(self):
        """
        Test GET /api/football/live/territorial_control/history:
        - Should return {ok: true, count: N, items: [...]}
        - Items should be sorted by generated_at desc
        """
        self.log("\n" + "="*70)
        self.log("TEST 5: History Endpoint")
        self.log("="*70)

        try:
            resp = requests.get(
                f"{API_BASE}/football/live/territorial_control/history?limit=5",
                headers=self.headers(),
                timeout=10,
            )
            self.log(f"Response status: {resp.status_code}")
            
            if resp.status_code != 200:
                self.log(f"Response body: {resp.text}", "FAIL")
                self.assert_true(False, "History endpoint should return 200")
                return

            data = resp.json()
            self.assert_true(data.get("ok") is True, "History: ok=True")
            self.assert_true("count" in data, "History: count field present")
            self.assert_true("items" in data, "History: items field present")
            
            items = data.get("items", [])
            self.log(f"History returned {len(items)} items")
            
            # Check that items are sorted by generated_at desc
            if len(items) >= 2:
                first_time = items[0].get("generated_at", "")
                second_time = items[1].get("generated_at", "")
                self.assert_true(
                    first_time >= second_time,
                    f"History: items should be sorted desc (first: {first_time}, second: {second_time})"
                )
            
        except Exception as e:
            self.log(f"Exception in history test: {e}", "FAIL")
            self.assert_true(False, f"History test: Exception {e}")

    # ═══════════════════════════════════════════════════════════════════════
    # RUN ALL TESTS
    # ═══════════════════════════════════════════════════════════════════════

    def run_all_tests(self):
        """Run all backend tests."""
        self.log("\n" + "="*70)
        self.log("LIVE TERRITORIAL CONTROL + CORNER INTELLIGENCE BACKEND TESTS")
        self.log("="*70)
        
        if not self.login():
            self.log("Login failed, cannot proceed with tests", "FAIL")
            return False

        # Run all test methods
        self.test_psg_canonical_case()
        self.test_control_with_pressure_case()
        self.test_no_clear_dominance_case()
        self.test_api_validation()
        self.test_history_endpoint()

        # Print summary
        self.log("\n" + "="*70)
        self.log("TEST SUMMARY")
        self.log("="*70)
        self.log(f"Total tests run: {self.tests_run}")
        self.log(f"Tests passed: {self.tests_passed}", "PASS")
        self.log(f"Tests failed: {self.tests_failed}", "FAIL" if self.tests_failed > 0 else "INFO")
        
        if self.failures:
            self.log("\nFailed tests:", "FAIL")
            for failure in self.failures:
                self.log(f"  - {failure}", "FAIL")
        
        success_rate = (self.tests_passed / self.tests_run * 100) if self.tests_run > 0 else 0
        self.log(f"\nSuccess rate: {success_rate:.1f}%")
        
        return self.tests_failed == 0


def main():
    tester = LiveTerritorialControlTester()
    success = tester.run_all_tests()
    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
