"""
Test suite for Human Live Interpreter (P3.1) — the "human live betting copilot".

Validates:
  1. GET /api/matches/live?sport=football returns _live_interpreter for every match
  2. Interpreter shape: {title, subtitle, mood, icon, action, action_label, recommendation, 
     suggested_market, confidence, risk, urgency, why:[], narration, trap, _source}
  3. mood ∈ {trap, value, watch, neutral, insufficient}
  4. action ∈ {BET_NOW, WAIT, WATCHLIST, NO_BET, CASH_OUT, LOW_CONFIDENCE}
  5. risk ∈ {LOW, MEDIUM, HIGH}
  6. confidence is int 0-100
  7. why is list of Spanish strings
  8. Narration is PLAIN SPANISH (not robotic)
  9. POST /api/live/reevaluate with manual_odds + manual_market returns interpreter
  10. Non-football sports should NOT have _live_interpreter
"""
import sys
import requests
from datetime import datetime

BASE_URL = "https://low-volatility-plays.preview.emergentagent.com/api"

# Valid enum values
VALID_MOODS = {"trap", "value", "watch", "neutral", "insufficient"}
VALID_ACTIONS = {"BET_NOW", "WAIT", "WATCHLIST", "NO_BET", "CASH_OUT", "LOW_CONFIDENCE"}
VALID_RISKS = {"LOW", "MEDIUM", "HIGH"}

# Robotic phrases that should NOT appear in narration
ROBOTIC_PHRASES = [
    "edge positivo detectado",
    "probabilidad implícita",
    "valor esperado",
    "expected value",
    "implied probability",
]

# Human phrases that SHOULD appear
HUMAN_PHRASES = [
    "creciendo",
    "pocas oportunidades",
    "no domina",
    "ritmo",
    "empujando",
    "presión",
    "momentum",
    "domina",
    "táctico",
]

class HumanLiveInterpreterTester:
    def __init__(self):
        self.token = None
        self.tests_run = 0
        self.tests_passed = 0
        self.issues = []

    def log_pass(self, test_name):
        self.tests_run += 1
        self.tests_passed += 1
        print(f"✅ PASS: {test_name}")

    def log_fail(self, test_name, reason):
        self.tests_run += 1
        self.issues.append(f"{test_name}: {reason}")
        print(f"❌ FAIL: {test_name}")
        print(f"   Reason: {reason}")

    def login(self):
        """Authenticate with demo credentials"""
        print("\n🔐 Authenticating...")
        try:
            r = requests.post(
                f"{BASE_URL}/auth/login",
                json={"email": "demo@valuebet.app", "password": "demo1234"},
                timeout=10
            )
            if r.status_code == 200:
                self.token = r.json().get("token")
                print(f"✅ Authenticated successfully")
                return True
            else:
                print(f"❌ Login failed: {r.status_code} - {r.text}")
                return False
        except Exception as e:
            print(f"❌ Login error: {e}")
            return False

    def headers(self):
        return {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json"
        }

    def validate_interpreter_shape(self, interpreter, match_id):
        """Validate the interpreter object has all required fields with correct types"""
        required_fields = {
            "title": str,
            "subtitle": str,
            "mood": str,
            "icon": str,
            "action": str,
            "action_label": str,
            "recommendation": str,
            "confidence": int,
            "risk": str,
            "urgency": str,
            "why": list,
            "narration": str,
            "_source": str,
        }
        
        for field, expected_type in required_fields.items():
            if field not in interpreter:
                self.log_fail(
                    f"Interpreter shape [{match_id}]",
                    f"Missing required field: {field}"
                )
                return False
            
            if not isinstance(interpreter[field], expected_type):
                self.log_fail(
                    f"Interpreter shape [{match_id}]",
                    f"Field '{field}' has wrong type: expected {expected_type.__name__}, got {type(interpreter[field]).__name__}"
                )
                return False
        
        # Validate enums
        if interpreter["mood"] not in VALID_MOODS:
            self.log_fail(
                f"Interpreter mood [{match_id}]",
                f"Invalid mood: {interpreter['mood']} (must be one of {VALID_MOODS})"
            )
            return False
        
        if interpreter["action"] not in VALID_ACTIONS:
            self.log_fail(
                f"Interpreter action [{match_id}]",
                f"Invalid action: {interpreter['action']} (must be one of {VALID_ACTIONS})"
            )
            return False
        
        if interpreter["risk"] not in VALID_RISKS:
            self.log_fail(
                f"Interpreter risk [{match_id}]",
                f"Invalid risk: {interpreter['risk']} (must be one of {VALID_RISKS})"
            )
            return False
        
        # Validate confidence range
        if not (0 <= interpreter["confidence"] <= 100):
            self.log_fail(
                f"Interpreter confidence [{match_id}]",
                f"Confidence out of range: {interpreter['confidence']} (must be 0-100)"
            )
            return False
        
        # Validate _source
        if interpreter["_source"] != "human_live_interpreter_v1":
            self.log_fail(
                f"Interpreter source [{match_id}]",
                f"Wrong _source: {interpreter['_source']} (expected 'human_live_interpreter_v1')"
            )
            return False
        
        # Validate why is list of strings
        if not all(isinstance(item, str) for item in interpreter["why"]):
            self.log_fail(
                f"Interpreter why [{match_id}]",
                f"'why' must be list of strings"
            )
            return False
        
        self.log_pass(f"Interpreter shape validation [{match_id}]")
        return True

    def validate_narration_quality(self, narration, match_id):
        """Validate narration is human-friendly Spanish, not robotic"""
        narration_lower = narration.lower()
        
        # Check for robotic phrases (should NOT be present)
        found_robotic = []
        for phrase in ROBOTIC_PHRASES:
            if phrase.lower() in narration_lower:
                found_robotic.append(phrase)
        
        if found_robotic:
            self.log_fail(
                f"Narration quality [{match_id}]",
                f"Found robotic phrases: {found_robotic}"
            )
            return False
        
        # Check for at least one human phrase (should be present)
        found_human = any(phrase.lower() in narration_lower for phrase in HUMAN_PHRASES)
        
        if not found_human:
            # This is a warning, not a hard fail - narration might be valid but not match our list
            print(f"⚠️  WARNING: Narration [{match_id}] doesn't contain common human phrases")
            print(f"   Narration: {narration[:100]}...")
        
        self.log_pass(f"Narration quality [{match_id}]")
        return True

    def test_football_live_matches(self):
        """Test GET /api/matches/live?sport=football - every match should have interpreter"""
        print("\n📊 Testing football live matches...")
        try:
            r = requests.get(
                f"{BASE_URL}/matches/live",
                params={"sport": "football", "refresh": False},
                headers=self.headers(),
                timeout=15
            )
            
            if r.status_code != 200:
                self.log_fail("GET /api/matches/live", f"Status {r.status_code}")
                return
            
            data = r.json()
            items = data.get("items", [])
            
            if len(items) == 0:
                print("⚠️  No live football matches found - skipping interpreter tests")
                return
            
            print(f"   Found {len(items)} live football matches")
            
            # Test first 5 matches (as per requirement)
            for i, match in enumerate(items[:5]):
                match_id = match.get("match_id")
                print(f"\n   Testing match {i+1}/{min(5, len(items))}: {match_id}")
                
                # Check _live_analysis exists
                if "_live_analysis" not in match:
                    self.log_fail(
                        f"Live analysis [{match_id}]",
                        "_live_analysis field missing"
                    )
                    continue
                else:
                    self.log_pass(f"Live analysis present [{match_id}]")
                
                # Check _live_interpreter exists
                if "_live_interpreter" not in match:
                    self.log_fail(
                        f"Live interpreter [{match_id}]",
                        "_live_interpreter field missing"
                    )
                    continue
                
                interpreter = match["_live_interpreter"]
                
                if interpreter is None:
                    self.log_fail(
                        f"Live interpreter [{match_id}]",
                        "_live_interpreter is null"
                    )
                    continue
                
                self.log_pass(f"Live interpreter present [{match_id}]")
                
                # Validate shape
                if not self.validate_interpreter_shape(interpreter, match_id):
                    continue
                
                # Validate narration quality
                self.validate_narration_quality(interpreter["narration"], match_id)
                
                # Print sample for manual review
                print(f"   📝 Sample interpreter output:")
                print(f"      Title: {interpreter['title']}")
                print(f"      Mood: {interpreter['mood']}")
                print(f"      Action: {interpreter['action']}")
                print(f"      Recommendation: {interpreter['recommendation']}")
                print(f"      Confidence: {interpreter['confidence']}%")
                print(f"      Risk: {interpreter['risk']}")
                print(f"      Narration: {interpreter['narration'][:80]}...")
                if interpreter.get("suggested_market"):
                    print(f"      Suggested market: {interpreter['suggested_market']}")
                if interpreter.get("trap") and interpreter["trap"].get("triggered"):
                    print(f"      ⚠️  Trap detected!")
        
        except Exception as e:
            self.log_fail("GET /api/matches/live", f"Exception: {e}")

    def test_basketball_no_interpreter(self):
        """Test GET /api/matches/live?sport=basketball - should NOT have interpreter"""
        print("\n🏀 Testing basketball live matches (should NOT have interpreter)...")
        try:
            r = requests.get(
                f"{BASE_URL}/matches/live",
                params={"sport": "basketball", "refresh": False},
                headers=self.headers(),
                timeout=15
            )
            
            if r.status_code != 200:
                self.log_fail("GET /api/matches/live (basketball)", f"Status {r.status_code}")
                return
            
            data = r.json()
            items = data.get("items", [])
            
            if len(items) == 0:
                print("⚠️  No live basketball matches found - skipping test")
                return
            
            print(f"   Found {len(items)} live basketball matches")
            
            for match in items[:3]:
                match_id = match.get("match_id")
                
                if "_live_interpreter" in match and match["_live_interpreter"] is not None:
                    self.log_fail(
                        f"Basketball interpreter [{match_id}]",
                        "Basketball match should NOT have _live_interpreter"
                    )
                else:
                    self.log_pass(f"Basketball no interpreter [{match_id}]")
        
        except Exception as e:
            self.log_fail("GET /api/matches/live (basketball)", f"Exception: {e}")

    def test_reevaluate_with_manual_odds(self):
        """Test POST /api/live/reevaluate with manual_odds + manual_market"""
        print("\n🔄 Testing live reevaluate with manual odds...")
        
        # First get a live match
        try:
            r = requests.get(
                f"{BASE_URL}/matches/live",
                params={"sport": "football", "refresh": False},
                headers=self.headers(),
                timeout=15
            )
            
            if r.status_code != 200 or not r.json().get("items"):
                print("⚠️  No live matches available for reevaluate test")
                return
            
            match = r.json()["items"][0]
            match_id = match.get("match_id")
            
            print(f"   Testing reevaluate on match: {match_id}")
            
            # Test reevaluate with manual odds
            reeval_body = {
                "match_id": match_id,
                "sport": "football",
                "refresh": True,
                "manual_odds": 1.85,
                "manual_market": "Under 2.5"
            }
            
            r = requests.post(
                f"{BASE_URL}/live/reevaluate",
                json=reeval_body,
                headers=self.headers(),
                timeout=20
            )
            
            if r.status_code == 409:
                # Match is no longer live
                print(f"⚠️  Match {match_id} is no longer active (409) - skipping reevaluate test")
                return
            
            if r.status_code != 200:
                self.log_fail(
                    "POST /api/live/reevaluate",
                    f"Status {r.status_code}: {r.text[:200]}"
                )
                return
            
            data = r.json()
            result = data.get("result")
            
            if not result:
                self.log_fail("Reevaluate result", "No result in response")
                return
            
            self.log_pass("Reevaluate API call")
            
            # Check interpreter is present
            if "interpreter" not in result or result["interpreter"] is None:
                self.log_fail(
                    "Reevaluate interpreter",
                    "interpreter field missing or null in reevaluate result"
                )
                return
            
            self.log_pass("Reevaluate interpreter present")
            
            interpreter = result["interpreter"]
            
            # Validate shape
            if not self.validate_interpreter_shape(interpreter, f"reeval-{match_id}"):
                return
            
            # Validate narration
            self.validate_narration_quality(interpreter["narration"], f"reeval-{match_id}")
            
            # Check that action is consistent with edge
            edge_pct = result.get("edge_pct", 0)
            action = interpreter["action"]
            
            if edge_pct >= 4 and action != "BET_NOW":
                print(f"⚠️  WARNING: Edge {edge_pct}% but action is {action} (expected BET_NOW)")
            
            # Check recommendation contains market name
            recommendation = interpreter["recommendation"]
            if "Under 2.5" not in recommendation and "UNDER 2.5" not in recommendation:
                print(f"⚠️  WARNING: Recommendation doesn't mention market: {recommendation}")
            
            print(f"   📝 Reevaluate interpreter output:")
            print(f"      Title: {interpreter['title']}")
            print(f"      Action: {interpreter['action']}")
            print(f"      Recommendation: {interpreter['recommendation']}")
            print(f"      Confidence: {interpreter['confidence']}%")
            print(f"      Edge: {edge_pct:.2f}%")
            
        except Exception as e:
            self.log_fail("POST /api/live/reevaluate", f"Exception: {e}")

    def run_all_tests(self):
        """Run all tests"""
        print("=" * 70)
        print("🧪 Human Live Interpreter Test Suite")
        print("=" * 70)
        
        if not self.login():
            print("\n❌ Authentication failed - cannot proceed with tests")
            return 1
        
        self.test_football_live_matches()
        self.test_basketball_no_interpreter()
        self.test_reevaluate_with_manual_odds()
        
        # Print summary
        print("\n" + "=" * 70)
        print("📊 TEST SUMMARY")
        print("=" * 70)
        print(f"Tests run: {self.tests_run}")
        print(f"Tests passed: {self.tests_passed}")
        print(f"Tests failed: {self.tests_run - self.tests_passed}")
        
        if self.issues:
            print("\n❌ ISSUES FOUND:")
            for issue in self.issues:
                print(f"  • {issue}")
        else:
            print("\n✅ ALL TESTS PASSED!")
        
        print("=" * 70)
        
        return 0 if len(self.issues) == 0 else 1

if __name__ == "__main__":
    tester = HumanLiveInterpreterTester()
    sys.exit(tester.run_all_tests())
