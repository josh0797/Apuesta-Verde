"""Phase 8 Backend Testing — Football Quality & Selection Engine.

Tests:
1. GET /api/picks/today?sport=football returns mock with Phase 8 metadata
2. services/football_quality.py unit tests for scoring logic
3. services/football_competitions.py tier classification
"""
import sys
import os
import requests

# Add backend to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from services import football_quality as fq
from services import football_competitions as fc

# Backend URL from env
BACKEND_URL = os.environ.get("REACT_APP_BACKEND_URL", "https://low-volatility-plays.preview.emergentagent.com")
API_BASE = f"{BACKEND_URL}/api"

# Test credentials
DEMO_EMAIL = "demo@valuebet.app"
DEMO_PASSWORD = "demo1234"


class Phase8Tester:
    def __init__(self):
        self.token = None
        self.tests_run = 0
        self.tests_passed = 0
        self.failures = []

    def log(self, msg, level="INFO"):
        prefix = "✅" if level == "PASS" else "❌" if level == "FAIL" else "🔍"
        print(f"{prefix} {msg}")

    def assert_true(self, condition, test_name, detail=""):
        self.tests_run += 1
        if condition:
            self.tests_passed += 1
            self.log(f"PASS: {test_name}", "PASS")
            return True
        else:
            self.log(f"FAIL: {test_name} — {detail}", "FAIL")
            self.failures.append(f"{test_name}: {detail}")
            return False

    def assert_equal(self, actual, expected, test_name):
        self.tests_run += 1
        if actual == expected:
            self.tests_passed += 1
            self.log(f"PASS: {test_name}", "PASS")
            return True
        else:
            self.log(f"FAIL: {test_name} — expected {expected}, got {actual}", "FAIL")
            self.failures.append(f"{test_name}: expected {expected}, got {actual}")
            return False

    def assert_in(self, item, collection, test_name):
        self.tests_run += 1
        if item in collection:
            self.tests_passed += 1
            self.log(f"PASS: {test_name}", "PASS")
            return True
        else:
            self.log(f"FAIL: {test_name} — {item} not in {collection}", "FAIL")
            self.failures.append(f"{test_name}: {item} not in {collection}")
            return False

    def login(self):
        """Login and get token"""
        self.log("Logging in as demo@valuebet.app...")
        try:
            r = requests.post(f"{API_BASE}/auth/login", json={"email": DEMO_EMAIL, "password": DEMO_PASSWORD}, timeout=10)
            if r.status_code == 200:
                data = r.json()
                self.token = data.get("token")
                self.log(f"Login successful, token: {self.token[:20]}...")
                return True
            else:
                self.log(f"Login failed: {r.status_code} {r.text}", "FAIL")
                return False
        except Exception as e:
            self.log(f"Login error: {e}", "FAIL")
            return False

    def test_api_picks_today(self):
        """Test GET /api/picks/today?sport=football returns Phase 8 mock"""
        self.log("\n=== Testing GET /api/picks/today?sport=football ===")
        try:
            headers = {"Authorization": f"Bearer {self.token}"}
            r = requests.get(f"{API_BASE}/picks/today", params={"sport": "football"}, headers=headers, timeout=10)
            self.assert_equal(r.status_code, 200, "GET /api/picks/today status 200")
            
            data = r.json()
            pick_run = data.get("pick_run")
            self.assert_true(pick_run is not None, "pick_run exists")
            
            if not pick_run:
                return
            
            payload = pick_run.get("payload", {})
            summary = payload.get("summary", {})
            
            # Check skipped_low_relevance
            skipped = summary.get("skipped_low_relevance", [])
            self.assert_equal(len(skipped), 4, "skipped_low_relevance has 4 entries")
            
            # Check states
            states = [s.get("state") for s in skipped]
            valid_states = {"EXOTIC_LEAGUE_WARNING", "LOW_MARKET_SUPPORT", "LOW_DATA_QUALITY"}
            for i, state in enumerate(states):
                self.assert_in(state, valid_states, f"skipped[{i}].state is valid ({state})")
            
            # Check cascade_used
            pipeline = payload.get("_pipeline", {})
            fq_stats = pipeline.get("football_quality", {})
            cascade = fq_stats.get("cascade_used", [])
            self.assert_equal(cascade, [1, 2], "cascade_used == [1, 2]")
            
            # Check picks have _football_quality
            picks = payload.get("picks", [])
            self.assert_true(len(picks) >= 4, f"picks has at least 4 entries (got {len(picks)})")
            
            for i, pick in enumerate(picks[:4]):
                fq_data = pick.get("_football_quality")
                self.assert_true(fq_data is not None, f"pick[{i}]._football_quality exists")
                if fq_data:
                    self.assert_true("state" in fq_data, f"pick[{i}]._football_quality.state exists")
                    self.assert_true("tier" in fq_data, f"pick[{i}]._football_quality.tier exists")
                    self.assert_true("score" in fq_data, f"pick[{i}]._football_quality.score exists")
                    self.assert_true("market_liquidity" in fq_data, f"pick[{i}]._football_quality.market_liquidity exists")
                    self.assert_true("league_quality" in fq_data, f"pick[{i}]._football_quality.league_quality exists")
            
        except Exception as e:
            self.log(f"API test error: {e}", "FAIL")
            self.failures.append(f"API test error: {e}")

    def test_football_quality_unit(self):
        """Unit tests for services/football_quality.py"""
        self.log("\n=== Testing services/football_quality.py ===")
        
        # Test 1: Premier League (league_id:39) → PRIORITY_MATCH, tier 1, score>=80
        self.log("Test: Premier League (league_id:39)")
        match1 = {
            "league": "Premier League",
            "league_id": 39,
            "odds": {
                "moneyline": {"home": 1.85, "away": 2.10, "draw": 3.50},
                "bookmakers": [{"name": f"Book{i}"} for i in range(8)],
            },
            "home_team": {"stats": {"goals": 50}},
            "away_team": {"stats": {"goals": 45}},
            "lineups": True,
        }
        result1 = fq.compute_football_selection_score(match1)
        self.assert_equal(result1["tier"], 1, "Premier League tier == 1")
        self.assert_equal(result1["state"], "PRIORITY_MATCH", "Premier League state == PRIORITY_MATCH")
        self.assert_true(result1["score"] >= 80, f"Premier League score >= 80 (got {result1['score']})")
        
        # Test 2: Botswana Premier League (league_id:561) → tier 4, EXOTIC_LEAGUE_WARNING
        self.log("Test: Botswana Premier League (league_id:561)")
        match2 = {
            "league": "Botswana Premier League",
            "league_id": 561,
            "odds": {},
        }
        result2 = fq.compute_football_selection_score(match2)
        self.assert_equal(result2["tier"], 4, "Botswana Premier League tier == 4")
        self.assert_equal(result2["state"], "EXOTIC_LEAGUE_WARNING", "Botswana Premier League state == EXOTIC_LEAGUE_WARNING")
        
        # Test 3: Belarus Reserve League → tier 4, exotic
        self.log("Test: Belarus Reserve League")
        match3 = {
            "league": "Belarus Reserve League",
            "odds": {},
        }
        result3 = fq.compute_football_selection_score(match3)
        self.assert_equal(result3["tier"], 4, "Belarus Reserve League tier == 4")
        self.assert_true(result3["is_exotic"], "Belarus Reserve League is_exotic == True")
        
        # Test 4: La Liga (league_id:140) → tier 1
        self.log("Test: La Liga (league_id:140)")
        match4 = {
            "league": "La Liga",
            "league_id": 140,
            "odds": {
                "moneyline": {"home": 1.90},
                "bookmakers": [{"name": f"Book{i}"} for i in range(5)],
            },
        }
        result4 = fq.compute_football_selection_score(match4)
        self.assert_equal(result4["tier"], 1, "La Liga tier == 1")
        
        # Test 5: filter_and_prioritize
        self.log("Test: filter_and_prioritize")
        matches = [
            {"league": "Premier League", "league_id": 39, "odds": {"moneyline": {"home": 1.85}, "bookmakers": [{}]*8}, "home_team": {"stats": {}}, "away_team": {"stats": {}}},
            {"league": "Botswana Premier League", "league_id": 561, "odds": {}},
            {"league": "La Liga", "league_id": 140, "odds": {"moneyline": {"home": 1.90}, "bookmakers": [{}]*5}},
            {"league": "Belarus Reserve League", "odds": {}},
        ]
        result5 = fq.filter_and_prioritize(matches, target_count=3, enable_tier_4=False)
        selected = result5["selected"]
        skipped = result5["skipped"]
        stats = result5["stats"]
        
        self.assert_true(len(selected) >= 2, f"filter_and_prioritize selected >= 2 (got {len(selected)})")
        self.assert_true(len(skipped) >= 2, f"filter_and_prioritize skipped >= 2 (got {len(skipped)})")
        self.assert_true(1 in stats["cascade_used"], "cascade_used contains tier 1")
        
        # Check selected are ordered by score desc
        if len(selected) >= 2:
            scores = [s.get("_football_quality", {}).get("score", 0) for s in selected]
            self.assert_true(scores[0] >= scores[1], f"Selected ordered by score desc ({scores})")

    def test_football_competitions_unit(self):
        """Unit tests for services/football_competitions.py"""
        self.log("\n=== Testing services/football_competitions.py ===")
        
        # Test: Botswana Premier League should NOT return tier_1
        self.log("Test: get_competition_meta('Botswana Premier League')")
        meta = fc.get_competition_meta("Botswana Premier League")
        if meta:
            self.assert_true(meta.get("tier") != "tier_1", f"Botswana Premier League tier != tier_1 (got {meta.get('tier')})")
        else:
            self.log("Botswana Premier League not in allowlist (expected)", "PASS")
            self.tests_passed += 1
            self.tests_run += 1
        
        # Test: Premier League should return tier_1
        self.log("Test: get_competition_meta('Premier League')")
        meta2 = fc.get_competition_meta("Premier League")
        self.assert_true(meta2 is not None, "Premier League meta exists")
        if meta2:
            self.assert_equal(meta2.get("tier"), "tier_1", "Premier League tier == tier_1")
        
        # Test: La Liga should return tier_1
        self.log("Test: get_competition_meta('La Liga')")
        meta3 = fc.get_competition_meta("La Liga")
        self.assert_true(meta3 is not None, "La Liga meta exists")
        if meta3:
            self.assert_equal(meta3.get("tier"), "tier_1", "La Liga tier == tier_1")

    def print_summary(self):
        print("\n" + "="*60)
        print(f"📊 Phase 8 Backend Tests Summary")
        print("="*60)
        print(f"Tests run: {self.tests_run}")
        print(f"Tests passed: {self.tests_passed}")
        print(f"Tests failed: {self.tests_run - self.tests_passed}")
        print(f"Success rate: {(self.tests_passed / self.tests_run * 100):.1f}%")
        
        if self.failures:
            print("\n❌ Failures:")
            for f in self.failures:
                print(f"  - {f}")
        else:
            print("\n✅ All tests passed!")
        
        return 0 if self.tests_passed == self.tests_run else 1


def main():
    tester = Phase8Tester()
    
    # Login
    if not tester.login():
        print("❌ Login failed, cannot proceed")
        return 1
    
    # Run tests
    tester.test_api_picks_today()
    tester.test_football_quality_unit()
    tester.test_football_competitions_unit()
    
    # Summary
    return tester.print_summary()


if __name__ == "__main__":
    sys.exit(main())
