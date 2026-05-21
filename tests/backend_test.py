"""Backend API tests for sport tracking feature and humanizeSelection integration."""
import requests
import sys
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
        print(f"\n{'='*60}")
        print(f"🔍 Test {self.tests_run}: {name}")
        print('='*60)
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
        print(f"\n{'='*60}")
        print(f"📊 TEST SUMMARY")
        print('='*60)
        print(f"Total: {self.tests_run}")
        print(f"Passed: {self.tests_passed} ✅")
        print(f"Failed: {self.tests_failed} ❌")
        print(f"Success Rate: {(self.tests_passed/self.tests_run*100):.1f}%")
        
        if self.failures:
            print(f"\n{'='*60}")
            print("❌ FAILURES:")
            print('='*60)
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
    
    # ── Test 2: Get today's picks to find a valid match_id ──────────────────
    test_match_id = None
    test_run_id = None
    
    def test_get_picks_today():
        nonlocal test_match_id, test_run_id
        print("Fetching today's picks for football...")
        response = requests.get(f"{BASE_URL}/picks/today?sport=football", headers=headers)
        print(f"Status: {response.status_code}")
        assert response.status_code == 200, f"Failed to get picks: {response.status_code}"
        
        data = response.json()
        print(f"Response keys: {data.keys()}")
        
        pick_run = data.get("pick_run")
        if pick_run:
            test_run_id = pick_run.get("id")
            picks = pick_run.get("payload", {}).get("picks", [])
            if picks:
                test_match_id = picks[0].get("match_id")
                print(f"✓ Found run_id: {test_run_id}")
                print(f"✓ Found match_id: {test_match_id}")
            else:
                print("⚠ No picks found in pick_run")
        else:
            print("⚠ No pick_run found (user may not have generated picks yet)")
    
    runner.test("Get today's picks", test_get_picks_today)
    
    # ── Test 3: Track pick WITH sport field (football) ──────────────────────
    def test_track_with_sport_football():
        if not test_match_id or not test_run_id:
            print("⚠ Skipping: no match_id/run_id available")
            return
        
        print(f"Tracking pick with sport='football'...")
        payload = {
            "run_id": test_run_id,
            "match_id": test_match_id,
            "market": "Doble Oportunidad",
            "selection": "Home/Draw",
            "confidence_score": 75,
            "outcome": "won",
            "odds": 1.85,
            "league": "Test League",
            "match_label": "Test Match",
            "sport": "football"
        }
        response = requests.post(f"{BASE_URL}/picks/track", json=payload, headers=headers)
        print(f"Status: {response.status_code}")
        print(f"Response: {response.json()}")
        
        assert response.status_code == 200, f"Track failed: {response.status_code}"
        data = response.json()
        assert data.get("ok") == True, "Response ok != True"
        assert data.get("sport") == "football", f"Expected sport='football', got '{data.get('sport')}'"
        print(f"✓ Sport field returned: {data.get('sport')}")
    
    runner.test("Track pick with sport=football", test_track_with_sport_football)
    
    # ── Test 4: Track pick WITH sport field (basketball) ────────────────────
    def test_track_with_sport_basketball():
        if not test_match_id or not test_run_id:
            print("⚠ Skipping: no match_id/run_id available")
            return
        
        print(f"Tracking pick with sport='basketball'...")
        payload = {
            "run_id": test_run_id,
            "match_id": f"{test_match_id}_nba",  # Different match_id to avoid unique constraint
            "market": "Moneyline",
            "selection": "Home",
            "confidence_score": 80,
            "outcome": "lost",
            "odds": 2.10,
            "league": "NBA",
            "match_label": "Lakers vs Celtics",
            "sport": "basketball"
        }
        response = requests.post(f"{BASE_URL}/picks/track", json=payload, headers=headers)
        print(f"Status: {response.status_code}")
        print(f"Response: {response.json()}")
        
        assert response.status_code == 200, f"Track failed: {response.status_code}"
        data = response.json()
        assert data.get("ok") == True, "Response ok != True"
        assert data.get("sport") == "basketball", f"Expected sport='basketball', got '{data.get('sport')}'"
        print(f"✓ Sport field returned: {data.get('sport')}")
    
    runner.test("Track pick with sport=basketball", test_track_with_sport_basketball)
    
    # ── Test 5: Track pick WITH sport field (baseball) ──────────────────────
    def test_track_with_sport_baseball():
        if not test_match_id or not test_run_id:
            print("⚠ Skipping: no match_id/run_id available")
            return
        
        print(f"Tracking pick with sport='baseball'...")
        payload = {
            "run_id": test_run_id,
            "match_id": f"{test_match_id}_mlb",  # Different match_id
            "market": "Total Over",
            "selection": "Over 8.5",
            "confidence_score": 70,
            "outcome": "push",
            "odds": 1.95,
            "league": "MLB",
            "match_label": "Yankees vs Red Sox",
            "sport": "baseball"
        }
        response = requests.post(f"{BASE_URL}/picks/track", json=payload, headers=headers)
        print(f"Status: {response.status_code}")
        print(f"Response: {response.json()}")
        
        assert response.status_code == 200, f"Track failed: {response.status_code}"
        data = response.json()
        assert data.get("ok") == True, "Response ok != True"
        assert data.get("sport") == "baseball", f"Expected sport='baseball', got '{data.get('sport')}'"
        print(f"✓ Sport field returned: {data.get('sport')}")
    
    runner.test("Track pick with sport=baseball", test_track_with_sport_baseball)
    
    # ── Test 6: Track pick WITHOUT sport field (backward compatibility) ─────
    def test_track_without_sport():
        if not test_match_id or not test_run_id:
            print("⚠ Skipping: no match_id/run_id available")
            return
        
        print(f"Tracking pick WITHOUT sport field (backward compat)...")
        payload = {
            "run_id": test_run_id,
            "match_id": f"{test_match_id}_noSport",
            "market": "1X2",
            "selection": "Home",
            "confidence_score": 65,
            "outcome": "pending",
            "odds": 2.50,
            "league": "Test League",
            "match_label": "Test Match No Sport"
        }
        response = requests.post(f"{BASE_URL}/picks/track", json=payload, headers=headers)
        print(f"Status: {response.status_code}")
        print(f"Response: {response.json()}")
        
        assert response.status_code == 200, f"Track failed: {response.status_code}"
        data = response.json()
        assert data.get("ok") == True, "Response ok != True"
        # Should default to 'football'
        assert data.get("sport") == "football", f"Expected default sport='football', got '{data.get('sport')}'"
        print(f"✓ Defaulted to sport: {data.get('sport')}")
    
    runner.test("Track pick without sport (backward compat)", test_track_without_sport)
    
    # ── Test 7: Track pick with INVALID sport field ─────────────────────────
    def test_track_with_invalid_sport():
        if not test_match_id or not test_run_id:
            print("⚠ Skipping: no match_id/run_id available")
            return
        
        print(f"Tracking pick with INVALID sport='hockey'...")
        payload = {
            "run_id": test_run_id,
            "match_id": f"{test_match_id}_invalid",
            "market": "Moneyline",
            "selection": "Away",
            "confidence_score": 60,
            "outcome": "pending",
            "odds": 1.75,
            "league": "NHL",
            "match_label": "Invalid Sport Test",
            "sport": "hockey"  # Not supported
        }
        response = requests.post(f"{BASE_URL}/picks/track", json=payload, headers=headers)
        print(f"Status: {response.status_code}")
        print(f"Response: {response.json()}")
        
        assert response.status_code == 200, f"Track failed: {response.status_code}"
        data = response.json()
        assert data.get("ok") == True, "Response ok != True"
        # Should default to 'football' for invalid sport
        assert data.get("sport") == "football", f"Expected default sport='football' for invalid sport, got '{data.get('sport')}'"
        print(f"✓ Invalid sport defaulted to: {data.get('sport')}")
    
    runner.test("Track pick with invalid sport", test_track_with_invalid_sport)
    
    # ── Test 8: Verify tracked picks include sport field ────────────────────
    def test_list_tracked_picks():
        print("Fetching tracked picks to verify sport field is saved...")
        response = requests.get(f"{BASE_URL}/picks/tracked?limit=10", headers=headers)
        print(f"Status: {response.status_code}")
        
        assert response.status_code == 200, f"Failed to get tracked picks: {response.status_code}"
        data = response.json()
        items = data.get("items", [])
        print(f"✓ Found {len(items)} tracked picks")
        
        if items:
            # Check that recent picks have sport field
            recent = items[0]
            print(f"Most recent pick: {recent.get('match_label')}")
            print(f"  - sport: {recent.get('sport')}")
            print(f"  - market: {recent.get('market')}")
            print(f"  - outcome: {recent.get('outcome')}")
            
            # Verify sport field exists
            assert "sport" in recent, "Sport field not found in tracked pick"
            print(f"✓ Sport field present in tracked picks")
    
    runner.test("Verify tracked picks include sport field", test_list_tracked_picks)
    
    # ── Test 9: Test /picks/today with different sports ─────────────────────
    def test_picks_today_sport_filter():
        print("Testing /picks/today with sport parameter...")
        
        for sport in ["football", "basketball", "baseball"]:
            print(f"\n  Testing sport={sport}...")
            response = requests.get(f"{BASE_URL}/picks/today?sport={sport}", headers=headers)
            print(f"  Status: {response.status_code}")
            assert response.status_code == 200, f"Failed for sport={sport}"
            
            data = response.json()
            assert "sport" in data, f"No sport field in response for {sport}"
            assert data["sport"] == sport, f"Expected sport={sport}, got {data['sport']}"
            print(f"  ✓ sport={sport} returned correctly")
    
    runner.test("Test /picks/today sport filtering", test_picks_today_sport_filter)
    
    return runner.summary()


if __name__ == "__main__":
    sys.exit(main())
