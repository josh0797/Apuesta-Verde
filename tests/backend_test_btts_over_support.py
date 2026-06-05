"""Backend API tests for BTTS detection fix and Over Support features.

Tests the bug fix for Mexico vs Serbia case where BTTS YES recommendation
wasn't persisted because the market was hidden in narrative fields.

Also tests the P1 Over Support integration in football market selection.
"""
import requests
import sys
import time
from datetime import datetime

BASE_URL = "https://low-volatility-plays.preview.emergentagent.com/api"

class TestRunner:
    def __init__(self):
        self.token = None
        self.tests_run = 0
        self.tests_passed = 0
        self.tests_failed = 0
        self.failures = []

    def test(self, name, fn):
        """Run a single test function."""
        self.tests_run += 1
        print(f"\n{'='*70}")
        print(f"🔍 Test {self.tests_run}: {name}")
        print('='*70)
        try:
            fn()
            self.tests_passed += 1
            print(f"✅ PASSED")
        except AssertionError as e:
            self.tests_failed += 1
            self.failures.append(f"{name}: {str(e)}")
            print(f"❌ FAILED: {str(e)}")
        except Exception as e:
            self.tests_failed += 1
            self.failures.append(f"{name}: {str(e)}")
            print(f"❌ ERROR: {str(e)}")

    def summary(self):
        """Print test summary."""
        print(f"\n{'='*70}")
        print(f"📊 TEST SUMMARY")
        print('='*70)
        print(f"Total: {self.tests_run}")
        print(f"Passed: {self.tests_passed} ✅")
        print(f"Failed: {self.tests_failed} ❌")
        print(f"Success Rate: {(self.tests_passed/self.tests_run*100):.1f}%")
        
        if self.failures:
            print(f"\n{'='*70}")
            print("❌ FAILURES:")
            print('='*70)
            for f in self.failures:
                print(f"  • {f}")
        
        return 0 if self.tests_failed == 0 else 1


def main():
    runner = TestRunner()
    
    # ── Test 1: Login ────────────────────────────────────────────────────────
    def test_login():
        print("Attempting login with demo@valuebet.app...")
        response = requests.post(
            f"{BASE_URL}/auth/login",
            json={"email": "demo@valuebet.app", "password": "demo1234"}
        )
        print(f"Status: {response.status_code}")
        assert response.status_code == 200, f"Login failed with status {response.status_code}"
        
        data = response.json()
        assert "token" in data, "No token in response"
        runner.token = data["token"]
        print(f"✓ Got token: {runner.token[:20]}...")
    
    runner.test("Login with demo user", test_login)
    
    if not runner.token:
        print("\n❌ Cannot proceed without authentication token")
        return runner.summary()
    
    headers = {"Authorization": f"Bearer {runner.token}"}
    
    # ── Test 2: Verify Mexico vs Serbia backfill event exists ───────────────
    def test_mexico_serbia_backfill():
        print("Checking for Mexico vs Serbia backfill event (ID: 277b754d-7ebe-42e6-8a51-ec2893b129ad)...")
        response = requests.get(
            f"{BASE_URL}/live/recommendation-events?match_id=1528284&sport=football",
            headers=headers
        )
        print(f"Status: {response.status_code}")
        assert response.status_code == 200, f"Failed to get events: {response.status_code}"
        
        data = response.json()
        print(f"Response: ok={data.get('ok')}, count={data.get('count')}")
        
        assert data.get("ok") == True, "Response ok != True"
        items = data.get("items", [])
        
        # Find the backfill event
        backfill_event = None
        for item in items:
            if item.get("event_id") == "277b754d-7ebe-42e6-8a51-ec2893b129ad":
                backfill_event = item
                break
        
        if backfill_event:
            print(f"✓ Found backfill event")
            print(f"  - status: {backfill_event.get('status')}")
            print(f"  - normalized_market: {backfill_event.get('recommendation', {}).get('normalized_market')}")
            print(f"  - settled_score: {backfill_event.get('outcome', {}).get('settled_score')}")
            
            # Verify it's a BTTS YES event that was settled as hit
            rec = backfill_event.get("recommendation", {})
            assert rec.get("normalized_market") == "BTTS_YES", f"Expected BTTS_YES, got {rec.get('normalized_market')}"
            assert backfill_event.get("status") == "hit", f"Expected status=hit, got {backfill_event.get('status')}"
            assert backfill_event.get("outcome", {}).get("settled_score") == "1-1", "Expected settled_score=1-1"
            print(f"✓ Backfill event verified: BTTS YES @ 0-1, settled as hit @ 1-1")
        else:
            print(f"⚠ Backfill event not found (may need to be created)")
    
    runner.test("Verify Mexico vs Serbia backfill event", test_mexico_serbia_backfill)
    
    # ── Test 3: Manual event creation with BTTS detection ───────────────────
    def test_manual_event_btts_detection():
        print("Creating manual event with BTTS in narrative...")
        timestamp = int(time.time())
        payload = {
            "sport": "football",
            "match_id": f"test_btts_{timestamp}",
            "match_label": "Test Team A vs Test Team B",
            "league": "Test League",
            "minute": 25,
            "score": {"home": 0, "away": 1, "label": "0-1"},
            "recommendation": {
                "market": "BTTS (Ambos marcan)",
                "selection": "Sí",
                "title": "BTTS",
                "confidence": 70
            },
            "reason": "El equipo local está creciendo y ambos equipos marcan es probable.",
            "outcome": {
                "result": "pending"
            }
        }
        
        response = requests.post(
            f"{BASE_URL}/live/recommendation-events/manual",
            json=payload,
            headers=headers
        )
        print(f"Status: {response.status_code}")
        print(f"Response: {response.json()}")
        
        assert response.status_code == 200, f"Failed to create manual event: {response.status_code}"
        data = response.json()
        assert data.get("ok") == True, "Response ok != True"
        
        event = data.get("event")
        assert event is not None, "No event in response"
        
        rec = event.get("recommendation", {})
        print(f"✓ Event created with normalized_market: {rec.get('normalized_market')}")
        assert rec.get("normalized_market") == "BTTS_YES", f"Expected BTTS_YES, got {rec.get('normalized_market')}"
    
    runner.test("Manual event creation with BTTS detection", test_manual_event_btts_detection)
    
    # ── Test 4: Manual event with Over 2.5 detection ─────────────────────────
    def test_manual_event_over_detection():
        print("Creating manual event with Over 2.5 in narrative...")
        timestamp = int(time.time())
        payload = {
            "sport": "football",
            "match_id": f"test_over_{timestamp}",
            "match_label": "Test Team C vs Test Team D",
            "league": "Test League",
            "minute": 30,
            "score": {"home": 1, "away": 1, "label": "1-1"},
            "recommendation": {
                "market": "Más de 2.5 goles",
                "selection": "Over 2.5",
                "title": "Over 2.5",
                "confidence": 75
            },
            "reason": "El ritmo ofensivo apoya Over 2.5 con tiempo suficiente.",
            "outcome": {
                "result": "pending"
            }
        }
        
        response = requests.post(
            f"{BASE_URL}/live/recommendation-events/manual",
            json=payload,
            headers=headers
        )
        print(f"Status: {response.status_code}")
        
        assert response.status_code == 200, f"Failed to create manual event: {response.status_code}"
        data = response.json()
        assert data.get("ok") == True, "Response ok != True"
        
        event = data.get("event")
        rec = event.get("recommendation", {})
        print(f"✓ Event created with normalized_market: {rec.get('normalized_market')}")
        assert rec.get("normalized_market") == "OVER_2_5", f"Expected OVER_2_5, got {rec.get('normalized_market')}"
    
    runner.test("Manual event with Over 2.5 detection", test_manual_event_over_detection)
    
    # ── Test 5: Manual event with Under 3.5 detection ────────────────────────
    def test_manual_event_under_detection():
        print("Creating manual event with Under 3.5...")
        timestamp = int(time.time())
        payload = {
            "sport": "football",
            "match_id": f"test_under_{timestamp}",
            "match_label": "Test Team E vs Test Team F",
            "league": "Test League",
            "minute": 40,
            "score": {"home": 1, "away": 0, "label": "1-0"},
            "recommendation": {
                "market": "Menos de 3.5",
                "selection": "Under 3.5",
                "title": "Under 3.5",
                "confidence": 80
            },
            "reason": "Partido controlado, menos de 3.5 goles es seguro.",
            "outcome": {
                "result": "pending"
            }
        }
        
        response = requests.post(
            f"{BASE_URL}/live/recommendation-events/manual",
            json=payload,
            headers=headers
        )
        print(f"Status: {response.status_code}")
        
        assert response.status_code == 200, f"Failed to create manual event: {response.status_code}"
        data = response.json()
        assert data.get("ok") == True, "Response ok != True"
        
        event = data.get("event")
        rec = event.get("recommendation", {})
        print(f"✓ Event created with normalized_market: {rec.get('normalized_market')}")
        assert rec.get("normalized_market") == "UNDER_3_5", f"Expected UNDER_3_5, got {rec.get('normalized_market')}"
    
    runner.test("Manual event with Under 3.5 detection", test_manual_event_under_detection)
    
    # ── Test 6: GET with filters - status=hit ────────────────────────────────
    def test_get_events_filter_status_hit():
        print("Testing GET /live/recommendation-events?status=hit...")
        response = requests.get(
            f"{BASE_URL}/live/recommendation-events?status=hit&sport=football&limit=10",
            headers=headers
        )
        print(f"Status: {response.status_code}")
        
        assert response.status_code == 200, f"Failed to get events: {response.status_code}"
        data = response.json()
        assert data.get("ok") == True, "Response ok != True"
        
        items = data.get("items", [])
        print(f"✓ Found {len(items)} hit events")
        
        # Verify all items have status=hit
        for item in items:
            assert item.get("status") == "hit", f"Expected status=hit, got {item.get('status')}"
    
    runner.test("GET events with status=hit filter", test_get_events_filter_status_hit)
    
    # ── Test 7: GET with filters - result=hit ────────────────────────────────
    def test_get_events_filter_result_hit():
        print("Testing GET /live/recommendation-events?result=hit...")
        response = requests.get(
            f"{BASE_URL}/live/recommendation-events?result=hit&sport=football&limit=10",
            headers=headers
        )
        print(f"Status: {response.status_code}")
        
        assert response.status_code == 200, f"Failed to get events: {response.status_code}"
        data = response.json()
        assert data.get("ok") == True, "Response ok != True"
        
        items = data.get("items", [])
        print(f"✓ Found {len(items)} events with result=hit")
        
        # Verify all items have outcome.result=hit
        for item in items:
            outcome = item.get("outcome", {})
            assert outcome.get("result") == "hit", f"Expected result=hit, got {outcome.get('result')}"
    
    runner.test("GET events with result=hit filter", test_get_events_filter_result_hit)
    
    # ── Test 8: GET with filters - source=manual ─────────────────────────────
    def test_get_events_filter_source_manual():
        print("Testing GET /live/recommendation-events?source=manual...")
        response = requests.get(
            f"{BASE_URL}/live/recommendation-events?source=manual&sport=football&limit=10",
            headers=headers
        )
        print(f"Status: {response.status_code}")
        
        assert response.status_code == 200, f"Failed to get events: {response.status_code}"
        data = response.json()
        assert data.get("ok") == True, "Response ok != True"
        
        items = data.get("items", [])
        print(f"✓ Found {len(items)} manual events")
        
        # Verify all items have source=manual
        for item in items:
            assert item.get("source") == "manual", f"Expected source=manual, got {item.get('source')}"
    
    runner.test("GET events with source=manual filter", test_get_events_filter_source_manual)
    
    # ── Test 9: GET with filters - settled=true ──────────────────────────────
    def test_get_events_filter_settled_true():
        print("Testing GET /live/recommendation-events?settled=true...")
        response = requests.get(
            f"{BASE_URL}/live/recommendation-events?settled=true&sport=football&limit=10",
            headers=headers
        )
        print(f"Status: {response.status_code}")
        
        assert response.status_code == 200, f"Failed to get events: {response.status_code}"
        data = response.json()
        assert data.get("ok") == True, "Response ok != True"
        
        items = data.get("items", [])
        print(f"✓ Found {len(items)} settled events")
        
        # Verify all items have status in [hit, miss, push, void]
        for item in items:
            status = item.get("status")
            assert status in ["hit", "miss", "push", "void"], f"Expected settled status, got {status}"
    
    runner.test("GET events with settled=true filter", test_get_events_filter_settled_true)
    
    # ── Test 10: GET with limit parameter ────────────────────────────────────
    def test_get_events_limit():
        print("Testing GET /live/recommendation-events?limit=5...")
        response = requests.get(
            f"{BASE_URL}/live/recommendation-events?sport=football&limit=5",
            headers=headers
        )
        print(f"Status: {response.status_code}")
        
        assert response.status_code == 200, f"Failed to get events: {response.status_code}"
        data = response.json()
        assert data.get("ok") == True, "Response ok != True"
        
        items = data.get("items", [])
        print(f"✓ Found {len(items)} events (limit=5)")
        assert len(items) <= 5, f"Expected max 5 items, got {len(items)}"
    
    runner.test("GET events with limit parameter", test_get_events_limit)
    
    # ── Test 11: Fail-soft - invalid payload ─────────────────────────────────
    def test_failsoft_invalid_payload():
        print("Testing fail-soft with invalid payload (missing required fields)...")
        payload = {
            "sport": "football",
            # Missing match_id and recommendation
        }
        
        response = requests.post(
            f"{BASE_URL}/live/recommendation-events/manual",
            json=payload,
            headers=headers
        )
        print(f"Status: {response.status_code}")
        
        # Should return 422 (Pydantic validation) or 200 with ok=false
        assert response.status_code in [200, 422], f"Unexpected status: {response.status_code}"
        
        if response.status_code == 200:
            data = response.json()
            assert data.get("ok") == False, "Expected ok=false for invalid payload"
            print(f"✓ Fail-soft: returned ok=false")
        else:
            print(f"✓ Fail-soft: returned 422 validation error")
    
    runner.test("Fail-soft with invalid payload", test_failsoft_invalid_payload)
    
    # ── Test 12: No regression - /picks/today ────────────────────────────────
    def test_no_regression_picks_today():
        print("Testing no regression on /picks/today?sport=football...")
        response = requests.get(
            f"{BASE_URL}/picks/today?sport=football",
            headers=headers
        )
        print(f"Status: {response.status_code}")
        
        assert response.status_code == 200, f"Regression: /picks/today failed with {response.status_code}"
        data = response.json()
        print(f"✓ /picks/today still working (sport={data.get('sport')})")
    
    runner.test("No regression - /picks/today", test_no_regression_picks_today)
    
    # ── Test 13: No regression - /football/pattern-memory/summary ────────────
    def test_no_regression_pattern_memory():
        print("Testing no regression on /football/pattern-memory/summary...")
        response = requests.get(
            f"{BASE_URL}/football/pattern-memory/summary",
            headers=headers
        )
        print(f"Status: {response.status_code}")
        
        assert response.status_code == 200, f"Regression: pattern-memory failed with {response.status_code}"
        data = response.json()
        print(f"✓ /football/pattern-memory/summary still working (ok={data.get('ok')})")
    
    runner.test("No regression - /football/pattern-memory/summary", test_no_regression_pattern_memory)
    
    # ── Test 14: No regression - /football/totals-calibration/summary ────────
    def test_no_regression_totals_calibration():
        print("Testing no regression on /football/totals-calibration/summary...")
        response = requests.get(
            f"{BASE_URL}/football/totals-calibration/summary",
            headers=headers
        )
        print(f"Status: {response.status_code}")
        
        assert response.status_code == 200, f"Regression: totals-calibration failed with {response.status_code}"
        data = response.json()
        print(f"✓ /football/totals-calibration/summary still working (ok={data.get('ok')})")
    
    runner.test("No regression - /football/totals-calibration/summary", test_no_regression_totals_calibration)
    
    # ── Test 15: BTTS NO detection ───────────────────────────────────────────
    def test_btts_no_detection():
        print("Creating manual event with BTTS NO...")
        timestamp = int(time.time())
        payload = {
            "sport": "football",
            "match_id": f"test_btts_no_{timestamp}",
            "match_label": "Test Team G vs Test Team H",
            "league": "Test League",
            "minute": 35,
            "score": {"home": 0, "away": 0, "label": "0-0"},
            "recommendation": {
                "market": "BTTS NO",
                "selection": "No",
                "title": "BTTS NO",
                "confidence": 65
            },
            "reason": "Ambos equipos no marcan es probable por la defensa sólida.",
            "outcome": {
                "result": "pending"
            }
        }
        
        response = requests.post(
            f"{BASE_URL}/live/recommendation-events/manual",
            json=payload,
            headers=headers
        )
        print(f"Status: {response.status_code}")
        
        assert response.status_code == 200, f"Failed to create manual event: {response.status_code}"
        data = response.json()
        assert data.get("ok") == True, "Response ok != True"
        
        event = data.get("event")
        rec = event.get("recommendation", {})
        print(f"✓ Event created with normalized_market: {rec.get('normalized_market')}")
        assert rec.get("normalized_market") == "BTTS_NO", f"Expected BTTS_NO, got {rec.get('normalized_market')}"
    
    runner.test("BTTS NO detection", test_btts_no_detection)
    
    # ── Test 16: Over 1.5 detection ──────────────────────────────────────────
    def test_over_1_5_detection():
        print("Creating manual event with Over 1.5...")
        timestamp = int(time.time())
        payload = {
            "sport": "football",
            "match_id": f"test_over_15_{timestamp}",
            "match_label": "Test Team I vs Test Team J",
            "league": "Test League",
            "minute": 20,
            "score": {"home": 0, "away": 0, "label": "0-0"},
            "recommendation": {
                "market": "Over 1.5",
                "selection": "Over 1.5",
                "title": "Over 1.5",
                "confidence": 70
            },
            "reason": "Más de 1.5 goles es probable.",
            "outcome": {
                "result": "pending"
            }
        }
        
        response = requests.post(
            f"{BASE_URL}/live/recommendation-events/manual",
            json=payload,
            headers=headers
        )
        print(f"Status: {response.status_code}")
        
        assert response.status_code == 200, f"Failed to create manual event: {response.status_code}"
        data = response.json()
        assert data.get("ok") == True, "Response ok != True"
        
        event = data.get("event")
        rec = event.get("recommendation", {})
        print(f"✓ Event created with normalized_market: {rec.get('normalized_market')}")
        assert rec.get("normalized_market") == "OVER_1_5", f"Expected OVER_1_5, got {rec.get('normalized_market')}"
    
    runner.test("Over 1.5 detection", test_over_1_5_detection)
    
    # ── Test 17: Under 2.5 detection ─────────────────────────────────────────
    def test_under_2_5_detection():
        print("Creating manual event with Under 2.5...")
        timestamp = int(time.time())
        payload = {
            "sport": "football",
            "match_id": f"test_under_25_{timestamp}",
            "match_label": "Test Team K vs Test Team L",
            "league": "Test League",
            "minute": 45,
            "score": {"home": 1, "away": 0, "label": "1-0"},
            "recommendation": {
                "market": "Menos de 2.5 goles",
                "selection": "Under 2.5",
                "title": "Under 2.5",
                "confidence": 75
            },
            "reason": "Menos de 2.5 goles es seguro.",
            "outcome": {
                "result": "pending"
            }
        }
        
        response = requests.post(
            f"{BASE_URL}/live/recommendation-events/manual",
            json=payload,
            headers=headers
        )
        print(f"Status: {response.status_code}")
        
        assert response.status_code == 200, f"Failed to create manual event: {response.status_code}"
        data = response.json()
        assert data.get("ok") == True, "Response ok != True"
        
        event = data.get("event")
        rec = event.get("recommendation", {})
        print(f"✓ Event created with normalized_market: {rec.get('normalized_market')}")
        assert rec.get("normalized_market") == "UNDER_2_5", f"Expected UNDER_2_5, got {rec.get('normalized_market')}"
    
    runner.test("Under 2.5 detection", test_under_2_5_detection)
    
    # ── Test 18: Both teams to score (English) detection ─────────────────────
    def test_both_teams_to_score_english():
        print("Creating manual event with 'Both teams to score'...")
        timestamp = int(time.time())
        payload = {
            "sport": "football",
            "match_id": f"test_btts_en_{timestamp}",
            "match_label": "Test Team M vs Test Team N",
            "league": "Test League",
            "minute": 28,
            "score": {"home": 0, "away": 1, "label": "0-1"},
            "recommendation": {
                "market": "Both teams to score",
                "selection": "Yes",
                "title": "BTTS",
                "confidence": 68
            },
            "reason": "Both teams to score is likely.",
            "outcome": {
                "result": "pending"
            }
        }
        
        response = requests.post(
            f"{BASE_URL}/live/recommendation-events/manual",
            json=payload,
            headers=headers
        )
        print(f"Status: {response.status_code}")
        
        assert response.status_code == 200, f"Failed to create manual event: {response.status_code}"
        data = response.json()
        assert data.get("ok") == True, "Response ok != True"
        
        event = data.get("event")
        rec = event.get("recommendation", {})
        print(f"✓ Event created with normalized_market: {rec.get('normalized_market')}")
        assert rec.get("normalized_market") == "BTTS_YES", f"Expected BTTS_YES, got {rec.get('normalized_market')}"
    
    runner.test("Both teams to score (English) detection", test_both_teams_to_score_english)
    
    return runner.summary()


if __name__ == "__main__":
    sys.exit(main())
