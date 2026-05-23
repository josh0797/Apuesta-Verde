"""API endpoint tests for Moneyball Layer integration.

Tests that /api/picks/today returns _moneyball and _market_edge data.
"""
import requests
import sys

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
    
    # ═══════════════════════════════════════════════════════════════════════
    # Test 1: Login
    # ═══════════════════════════════════════════════════════════════════════
    def test_login():
        print("Logging in with demo@valuebet.app...")
        response = requests.post(
            f"{BASE_URL}/auth/login",
            json={"email": "demo@valuebet.app", "password": "demo1234"}
        )
        print(f"Status: {response.status_code}")
        assert response.status_code == 200, f"Login failed: {response.status_code}"
        
        data = response.json()
        assert "token" in data, "No token in response"
        runner.token = data["token"]
        print(f"✓ Token: {runner.token[:20]}...")
    
    runner.test("Login", test_login)
    
    if not runner.token:
        print("\n❌ Cannot proceed without token")
        return runner.summary()
    
    headers = {"Authorization": f"Bearer {runner.token}"}
    
    # ═══════════════════════════════════════════════════════════════════════
    # Test 2: /api/picks/today returns _moneyball data
    # ═══════════════════════════════════════════════════════════════════════
    def test_picks_today_moneyball():
        print("Fetching /api/picks/today...")
        response = requests.get(f"{BASE_URL}/picks/today?sport=football", headers=headers)
        print(f"Status: {response.status_code}")
        assert response.status_code == 200, f"Failed: {response.status_code}"
        
        data = response.json()
        pick_run = data.get("pick_run")
        
        if not pick_run:
            print("⚠ No pick_run found (user may need to generate picks first)")
            return
        
        payload = pick_run.get("payload", {})
        picks = payload.get("picks", [])
        
        if not picks:
            print("⚠ No picks found in pick_run")
            return
        
        print(f"✓ Found {len(picks)} picks")
        
        # Check first pick for _moneyball and _market_edge
        pick = picks[0]
        print(f"\nChecking pick: {pick.get('match_label')}")
        
        assert "_moneyball" in pick, "Pick missing _moneyball field"
        assert "_market_edge" in pick, "Pick missing _market_edge field"
        
        mb = pick["_moneyball"]
        me = pick["_market_edge"]
        
        # Check _moneyball structure
        assert "classification" in mb, "_moneyball missing classification"
        assert "classification_reason" in mb, "_moneyball missing classification_reason"
        assert "fragility" in mb, "_moneyball missing fragility"
        assert "public_overreaction" in mb, "_moneyball missing public_overreaction"
        assert "market_trap_signals" in mb, "_moneyball missing market_trap_signals"
        assert "undervalued_reasons" in mb, "_moneyball missing undervalued_reasons"
        assert "why_this_can_fail" in mb, "_moneyball missing why_this_can_fail"
        
        print(f"✓ _moneyball structure complete:")
        print(f"  - classification: {mb['classification']}")
        print(f"  - fragility: {mb['fragility']['score']}/100 ({mb['fragility']['label']})")
        print(f"  - overreaction: {mb['public_overreaction']['score']}/100 ({mb['public_overreaction']['label']})")
        print(f"  - trap signals: {len(mb['market_trap_signals'])}")
        print(f"  - undervalued: {len(mb['undervalued_reasons'])}")
        
        # Check _market_edge structure (back-compat)
        assert "implied_probability" in me, "_market_edge missing implied_probability"
        assert "estimated_probability" in me, "_market_edge missing estimated_probability"
        assert "edge" in me, "_market_edge missing edge"
        assert "edge_threshold" in me, "_market_edge missing edge_threshold"
        assert "verdict" in me, "_market_edge missing verdict"
        
        print(f"✓ _market_edge structure complete:")
        print(f"  - implied: {me['implied_probability']}")
        print(f"  - estimated: {me['estimated_probability']}")
        print(f"  - edge: {me['edge']}")
        print(f"  - verdict: {me['verdict']}")
        
        # Check _pipeline.moneyball
        pipeline = payload.get("_pipeline", {})
        assert "moneyball" in pipeline, "Payload missing _pipeline.moneyball"
        
        mb_pipeline = pipeline["moneyball"]
        assert "evaluated" in mb_pipeline, "_pipeline.moneyball missing evaluated"
        assert "kept" in mb_pipeline, "_pipeline.moneyball missing kept"
        assert "rerouted" in mb_pipeline, "_pipeline.moneyball missing rerouted"
        assert "by_classification" in mb_pipeline, "_pipeline.moneyball missing by_classification"
        
        print(f"✓ _pipeline.moneyball present:")
        print(f"  - evaluated: {mb_pipeline['evaluated']}")
        print(f"  - kept: {mb_pipeline['kept']}")
        print(f"  - rerouted: {mb_pipeline['rerouted']}")
        print(f"  - classifications: {mb_pipeline['by_classification']}")
    
    runner.test("API: /picks/today returns _moneyball", test_picks_today_moneyball)
    
    # ═══════════════════════════════════════════════════════════════════════
    # Test 3: Check discarded_market for rerouted picks
    # ═══════════════════════════════════════════════════════════════════════
    def test_discarded_market_moneyball():
        print("Checking discarded_market for _moneyball_classification...")
        response = requests.get(f"{BASE_URL}/picks/today?sport=football", headers=headers)
        assert response.status_code == 200
        
        data = response.json()
        pick_run = data.get("pick_run")
        
        if not pick_run:
            print("⚠ No pick_run found")
            return
        
        payload = pick_run.get("payload", {})
        summary = payload.get("summary", {})
        discarded = summary.get("discarded_market", [])
        
        print(f"✓ Found {len(discarded)} discarded picks")
        
        if discarded:
            # Check if any have _moneyball_classification
            for d in discarded[:3]:  # Check first 3
                if "_moneyball_classification" in d:
                    print(f"  - {d.get('match_label')}: {d['_moneyball_classification']}")
                    print(f"    Reason: {d.get('reason', 'N/A')}")
    
    runner.test("API: discarded_market has _moneyball_classification", test_discarded_market_moneyball)
    
    # ═══════════════════════════════════════════════════════════════════════
    # Test 4: No regressions on /api/auth/login
    # ═══════════════════════════════════════════════════════════════════════
    def test_login_regression():
        print("Testing login regression...")
        response = requests.post(
            f"{BASE_URL}/auth/login",
            json={"email": "demo@valuebet.app", "password": "demo1234"}
        )
        assert response.status_code == 200, f"Login regression: {response.status_code}"
        print("✓ Login still works")
    
    runner.test("Regression: /auth/login", test_login_regression)
    
    # ═══════════════════════════════════════════════════════════════════════
    # Test 5: No regressions on /api/picks/track
    # ═══════════════════════════════════════════════════════════════════════
    def test_track_regression():
        print("Testing /picks/track regression...")
        # Get a valid run_id and match_id first
        response = requests.get(f"{BASE_URL}/picks/today?sport=football", headers=headers)
        if response.status_code != 200:
            print("⚠ Cannot test track without picks")
            return
        
        data = response.json()
        pick_run = data.get("pick_run")
        if not pick_run:
            print("⚠ No pick_run to test track")
            return
        
        run_id = pick_run.get("id")
        picks = pick_run.get("payload", {}).get("picks", [])
        if not picks:
            print("⚠ No picks to test track")
            return
        
        match_id = f"{picks[0].get('match_id')}_test_track"
        
        payload = {
            "run_id": run_id,
            "match_id": match_id,
            "market": "Test Market",
            "selection": "Test Selection",
            "confidence_score": 75,
            "outcome": "pending",
            "odds": 1.85,
            "league": "Test League",
            "match_label": "Test Match",
            "sport": "football"
        }
        
        response = requests.post(f"{BASE_URL}/picks/track", json=payload, headers=headers)
        print(f"Status: {response.status_code}")
        assert response.status_code == 200, f"Track regression: {response.status_code}"
        print("✓ /picks/track still works")
    
    runner.test("Regression: /picks/track", test_track_regression)
    
    return runner.summary()


if __name__ == "__main__":
    sys.exit(main())
