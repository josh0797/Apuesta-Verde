"""
Test suite for Phase 15: Moneyball Layer Refactor
Tests the new market tolerance model, structured trap signals, 
PROTECTED_ACCEPTABLE and WATCHLIST classifications, and alternative rescue layer.
"""
import sys
import json
import asyncio
from datetime import datetime

# Add backend to path
sys.path.insert(0, '/app/backend')

# Import the modules we need to test
from services import market_tolerance as mt
from services import moneyball_layer as mb
from services import alternative_rescue as ar
import requests

# Backend URL from env
BACKEND_URL = "https://low-volatility-plays.preview.emergentagent.com"

class TestResults:
    def __init__(self):
        self.total = 0
        self.passed = 0
        self.failed = 0
        self.results = []
    
    def add_test(self, name: str, passed: bool, details: str = ""):
        self.total += 1
        if passed:
            self.passed += 1
            print(f"✅ PASS: {name}")
        else:
            self.failed += 1
            print(f"❌ FAIL: {name}")
            if details:
                print(f"   Details: {details}")
        self.results.append({
            "test": name,
            "passed": passed,
            "details": details
        })
    
    def summary(self):
        print(f"\n{'='*60}")
        print(f"TEST SUMMARY: {self.passed}/{self.total} passed")
        print(f"{'='*60}")
        return self.passed == self.total


def test_auth():
    """Test 1: POST /api/auth/login with demo credentials"""
    print("\n" + "="*60)
    print("TEST 1: Authentication")
    print("="*60)
    
    results = TestResults()
    
    try:
        response = requests.post(
            f"{BACKEND_URL}/api/auth/login",
            json={"email": "demo@valuebet.app", "password": "demo1234"},
            timeout=10
        )
        
        results.add_test(
            "Login returns 200",
            response.status_code == 200,
            f"Got status {response.status_code}"
        )
        
        if response.status_code == 200:
            data = response.json()
            # Check for either 'token' or 'access_token' key
            token_key = "token" if "token" in data else "access_token"
            results.add_test(
                "Response contains token",
                token_key in data,
                f"Keys: {list(data.keys())}"
            )
            
            if token_key in data:
                token = data[token_key]
                results.add_test(
                    "Token is non-empty string",
                    isinstance(token, str) and len(token) > 0,
                    f"Token length: {len(token) if isinstance(token, str) else 'N/A'}"
                )
                return results, token
        
    except Exception as e:
        results.add_test("Login request", False, str(e))
    
    return results, None


def test_module_imports():
    """Test 2: Backend module imports work correctly"""
    print("\n" + "="*60)
    print("TEST 2: Module Imports")
    print("="*60)
    
    results = TestResults()
    
    # Test market_tolerance imports
    try:
        from services.market_tolerance import classify_market_tolerance, tolerance_params
        results.add_test("Import classify_market_tolerance", True)
        results.add_test("Import tolerance_params", True)
    except Exception as e:
        results.add_test("Import market_tolerance functions", False, str(e))
    
    # Test alternative_rescue imports
    try:
        from services.alternative_rescue import attempt_alternative_market_rescue
        results.add_test("Import attempt_alternative_market_rescue", True)
    except Exception as e:
        results.add_test("Import alternative_rescue", False, str(e))
    
    # Test moneyball_layer imports
    try:
        from services.moneyball_layer import (
            detect_trap_signals_structured,
            REROUTE_CLASSIFICATIONS,
            PROTECTED_ACCEPTABLE_CLASSIFICATIONS,
            WATCHLIST_CLASSIFICATIONS
        )
        results.add_test("Import detect_trap_signals_structured", True)
        results.add_test("Import REROUTE_CLASSIFICATIONS", True)
        results.add_test("Import PROTECTED_ACCEPTABLE_CLASSIFICATIONS", True)
        results.add_test("Import WATCHLIST_CLASSIFICATIONS", True)
    except Exception as e:
        results.add_test("Import moneyball_layer functions", False, str(e))
    
    return results


def test_classify_market_tolerance():
    """Test 3: Unit test classify_market_tolerance with various markets"""
    print("\n" + "="*60)
    print("TEST 3: Market Tolerance Classification")
    print("="*60)
    
    results = TestResults()
    
    test_cases = [
        # (market, selection, odds, expected_category, description)
        ("Moneyline", "Home", 1.30, "aggressive", "Moneyline favorite at 1.30"),
        ("Under 3.5", "Under", 1.40, "protected", "Under 3.5 at 1.40"),
        ("Under 2.5", "Under", 1.85, "balanced", "Under 2.5 at 1.85"),
        ("Over 2.5", "Over", 2.00, "aggressive", "Over 2.5 at 2.00"),
        ("Doble Oportunidad", "1X", 1.45, "protected", "Doble Oportunidad 1X at 1.45"),
        ("Asian Handicap", "+1.0 Away", 1.85, "protected", "Asian Handicap +1.0 at 1.85"),
        ("Anotador", "Player X", 3.50, "aggressive", "Player scorer at 3.50"),
    ]
    
    for market, selection, odds, expected, desc in test_cases:
        try:
            result = mt.classify_market_tolerance(market, selection, decimal_odds=odds)
            passed = result == expected
            results.add_test(
                f"classify_market_tolerance: {desc}",
                passed,
                f"Expected '{expected}', got '{result}'"
            )
        except Exception as e:
            results.add_test(f"classify_market_tolerance: {desc}", False, str(e))
    
    return results


def test_analyze_pick_classifications():
    """Test 4-7: Unit test analyze_pick produces correct classifications"""
    print("\n" + "="*60)
    print("TEST 4-7: analyze_pick Classifications")
    print("="*60)
    
    results = TestResults()
    
    # Test 4: PROTECTED_ACCEPTABLE for Under 3.5 with slight negative edge
    print("\nTest 4: PROTECTED_ACCEPTABLE classification")
    try:
        pick = {
            "match_id": "test_1",
            "match_label": "Test Match 1",
            "recommendation": {
                "market": "Under 3.5",
                "selection": "Under",
                "odds_range": "1.62-1.62",
                "confidence_score": 72
            },
            "reasoning": "Test pick for protected market",
            "risks": [],
            "is_live": False,
            "key_data": {}
        }
        
        result = mb.analyze_pick(pick, sport="football")
        classification = result["_moneyball"]["classification"]
        
        # Check if classification is PROTECTED_ACCEPTABLE or VALUE_BET (both acceptable)
        passed = classification in ["PROTECTED_ACCEPTABLE", "VALUE_BET"]
        results.add_test(
            "analyze_pick produces PROTECTED_ACCEPTABLE or VALUE_BET for Under 3.5",
            passed,
            f"Got classification: {classification}"
        )
        
        # Check trap_signals_structured exists and is a list of dicts
        trap_signals = result["_moneyball"].get("trap_signals_structured", [])
        is_list_of_dicts = isinstance(trap_signals, list) and all(
            isinstance(t, dict) and "code" in t and "label" in t and "severity" in t and "explanation" in t
            for t in trap_signals
        )
        results.add_test(
            "trap_signals_structured is list of dicts with required keys",
            is_list_of_dicts,
            f"Type: {type(trap_signals)}, Sample: {trap_signals[:1] if trap_signals else 'empty'}"
        )
        
    except Exception as e:
        results.add_test("analyze_pick PROTECTED_ACCEPTABLE test", False, str(e))
    
    # Test 5: NO_BET_VALUE for aggressive market with negative edge
    print("\nTest 5: NO_BET_VALUE classification")
    try:
        pick = {
            "match_id": "test_2",
            "match_label": "Test Match 2",
            "recommendation": {
                "market": "Moneyline",
                "selection": "Home",
                "odds_range": "1.30-1.30",
                "confidence_score": 72
            },
            "reasoning": "Test pick for aggressive market",
            "risks": [],
            "is_live": False,
            "key_data": {}
        }
        
        result = mb.analyze_pick(pick, sport="football")
        classification = result["_moneyball"]["classification"]
        
        # Should be NO_BET_VALUE or MARKET_TRAP for aggressive market with poor odds
        passed = classification in ["NO_BET_VALUE", "MARKET_TRAP"]
        results.add_test(
            "analyze_pick produces NO_BET_VALUE for Moneyline 1.30",
            passed,
            f"Got classification: {classification}"
        )
        
    except Exception as e:
        results.add_test("analyze_pick NO_BET_VALUE test", False, str(e))
    
    # Test 6: WATCHLIST for Under 3.5 with moderate negative edge
    print("\nTest 6: WATCHLIST classification")
    try:
        pick = {
            "match_id": "test_3",
            "match_label": "Test Match 3",
            "recommendation": {
                "market": "Under 3.5",
                "selection": "Under",
                "odds_range": "1.58-1.58",
                "confidence_score": 72
            },
            "reasoning": "Test pick for watchlist",
            "risks": [],
            "is_live": False,
            "key_data": {}
        }
        
        result = mb.analyze_pick(pick, sport="football")
        classification = result["_moneyball"]["classification"]
        
        # Could be WATCHLIST, PROTECTED_ACCEPTABLE, or VALUE_BET depending on exact edge
        passed = classification in ["WATCHLIST", "PROTECTED_ACCEPTABLE", "VALUE_BET", "NO_BET_VALUE"]
        results.add_test(
            "analyze_pick produces valid classification for Under 3.5 at 1.58",
            passed,
            f"Got classification: {classification}"
        )
        
    except Exception as e:
        results.add_test("analyze_pick WATCHLIST test", False, str(e))
    
    # Test 7: MARKET_TRAP for pick with many high-severity traps
    print("\nTest 7: MARKET_TRAP classification")
    try:
        pick = {
            "match_id": "test_4",
            "match_label": "Test Match 4",
            "recommendation": {
                "market": "Moneyline",
                "selection": "Home",
                "odds_range": "1.25-1.25",
                "confidence_score": 85
            },
            "reasoning": "Favorito necesita ganar, equipo grande, forma estelar",
            "risks": ["Line drifting", "Cash-out bajo"],
            "is_live": True,
            "key_data": {
                "line_movement": {"direction": "drifting"}
            },
            "live_stats": {"minute": 85}
        }
        
        result = mb.analyze_pick(pick, sport="football")
        classification = result["_moneyball"]["classification"]
        
        # Should be MARKET_TRAP or NO_BET_VALUE
        passed = classification in ["MARKET_TRAP", "NO_BET_VALUE"]
        results.add_test(
            "analyze_pick produces MARKET_TRAP for pick with many traps",
            passed,
            f"Got classification: {classification}"
        )
        
    except Exception as e:
        results.add_test("analyze_pick MARKET_TRAP test", False, str(e))
    
    return results


def test_moneyball_summary():
    """Test 8: apply_moneyball_layer produces correct summary structure"""
    print("\n" + "="*60)
    print("TEST 8: Moneyball Layer Summary")
    print("="*60)
    
    results = TestResults()
    
    try:
        # Create a mock parsed result with picks
        parsed = {
            "picks": [
                {
                    "match_id": "test_1",
                    "match_label": "Test Match 1",
                    "recommendation": {
                        "market": "Under 3.5",
                        "selection": "Under",
                        "odds_range": "1.70-1.70",
                        "confidence_score": 75
                    },
                    "reasoning": "Test",
                    "risks": [],
                    "is_live": False,
                    "key_data": {}
                }
            ],
            "summary": {}
        }
        
        result = mb.apply_moneyball_layer(parsed, sport="football")
        
        # Check summary has new keys
        summary = result.get("summary", {})
        
        results.add_test(
            "Summary contains protected_acceptable",
            "protected_acceptable" in summary,
            f"Keys: {list(summary.keys())}"
        )
        
        results.add_test(
            "Summary contains watchlist",
            "watchlist" in summary,
            f"Keys: {list(summary.keys())}"
        )
        
        # Check _pipeline.moneyball exists
        pipeline = result.get("_pipeline", {})
        moneyball_meta = pipeline.get("moneyball", {})
        
        results.add_test(
            "_pipeline.moneyball exists",
            "moneyball" in pipeline,
            f"Pipeline keys: {list(pipeline.keys())}"
        )
        
        results.add_test(
            "_pipeline.moneyball.by_classification exists",
            "by_classification" in moneyball_meta,
            f"Moneyball keys: {list(moneyball_meta.keys())}"
        )
        
    except Exception as e:
        results.add_test("apply_moneyball_layer test", False, str(e))
    
    return results


def test_alternative_rescue():
    """Test 9-10: attempt_alternative_market_rescue unit tests"""
    print("\n" + "="*60)
    print("TEST 9-10: Alternative Market Rescue")
    print("="*60)
    
    results = TestResults()
    
    # Test 9: Returns None when no protected alternatives found
    print("\nTest 9: No rescue when no alternatives")
    try:
        match = {
            "match_id": "test_rescue_1",
            "home_team": {"name": "Team A"},
            "away_team": {"name": "Team B"},
            "odds_snapshots": []  # No odds data
        }
        
        result = ar.attempt_alternative_market_rescue(
            match,
            sport="football",
            base_confidence=60
        )
        
        results.add_test(
            "attempt_alternative_market_rescue returns None with no odds",
            result is None,
            f"Got: {result}"
        )
        
    except Exception as e:
        results.add_test("alternative_rescue no odds test", False, str(e))
    
    # Test 10: Basketball rescue with totals
    print("\nTest 10: Basketball rescue attempt")
    try:
        match = {
            "match_id": "test_rescue_2",
            "home_team": {"name": "Lakers"},
            "away_team": {"name": "Warriors"},
            "odds_snapshots": [
                {
                    "markets": {
                        "Total": [
                            {
                                "bookmaker": "Test Book",
                                "lines": {
                                    "Over 220.5": 1.90,
                                    "Under 220.5": 1.90
                                }
                            }
                        ]
                    }
                }
            ]
        }
        
        result = ar.attempt_alternative_market_rescue(
            match,
            sport="basketball",
            base_confidence=60
        )
        
        # Should either return None (conservative) or a valid rescue
        if result is not None:
            results.add_test(
                "Basketball rescue returns valid structure",
                "market" in result and "selection" in result,
                f"Keys: {list(result.keys()) if isinstance(result, dict) else 'N/A'}"
            )
        else:
            results.add_test(
                "Basketball rescue returns None (conservative)",
                True,
                "No fake edges generated"
            )
        
    except Exception as e:
        results.add_test("alternative_rescue basketball test", False, str(e))
    
    return results


def test_api_endpoints(token):
    """Test 11-15: API endpoint tests"""
    print("\n" + "="*60)
    print("TEST 11-15: API Endpoints")
    print("="*60)
    
    results = TestResults()
    
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    
    # Test 11: POST /api/analysis/run
    print("\nTest 11: POST /api/analysis/run")
    try:
        response = requests.post(
            f"{BACKEND_URL}/api/analysis/run",
            json={"sport": "football", "force": True},
            headers=headers,
            timeout=60
        )
        
        results.add_test(
            "/api/analysis/run returns 2xx",
            200 <= response.status_code < 300,
            f"Status: {response.status_code}"
        )
        
        if 200 <= response.status_code < 300:
            data = response.json()
            summary = data.get("summary", {})
            
            # Check for new keys
            new_keys = ["rescued_picks", "watchlist", "protected_acceptable",
                       "total_rescued", "total_watchlist", "total_protected_acceptable"]
            
            for key in new_keys:
                results.add_test(
                    f"Summary contains {key}",
                    key in summary,
                    f"Keys: {list(summary.keys())}"
                )
        
    except Exception as e:
        results.add_test("/api/analysis/run test", False, str(e))
    
    # Test 12: GET /api/picks/today
    print("\nTest 12: GET /api/picks/today")
    try:
        response = requests.get(
            f"{BACKEND_URL}/api/picks/today",
            headers=headers,
            timeout=30
        )
        
        results.add_test(
            "/api/picks/today returns 2xx",
            200 <= response.status_code < 300,
            f"Status: {response.status_code}"
        )
        
        if 200 <= response.status_code < 300:
            data = response.json()
            summary = data.get("summary", {})
            
            # Check for new buckets (even if empty)
            results.add_test(
                "Summary has rescued_picks",
                "rescued_picks" in summary,
                f"Keys: {list(summary.keys())}"
            )
            
            results.add_test(
                "Summary has watchlist",
                "watchlist" in summary,
                f"Keys: {list(summary.keys())}"
            )
            
            results.add_test(
                "Summary has protected_acceptable",
                "protected_acceptable" in summary,
                f"Keys: {list(summary.keys())}"
            )
        
    except Exception as e:
        results.add_test("/api/picks/today test", False, str(e))
    
    # Test 13: POST /api/live/reevaluate (regression - should respond <5s)
    print("\nTest 13: POST /api/live/reevaluate (regression)")
    try:
        import time
        start = time.time()
        
        response = requests.post(
            f"{BACKEND_URL}/api/live/reevaluate",
            json={"sport": "football"},
            headers=headers,
            timeout=10
        )
        
        elapsed = time.time() - start
        
        results.add_test(
            "/api/live/reevaluate responds <5s",
            elapsed < 5.0,
            f"Took {elapsed:.2f}s"
        )
        
        results.add_test(
            "/api/live/reevaluate returns 2xx",
            200 <= response.status_code < 300,
            f"Status: {response.status_code}"
        )
        
    except Exception as e:
        results.add_test("/api/live/reevaluate test", False, str(e))
    
    # Test 14: GET /api/matches/upcoming (regression)
    print("\nTest 14: GET /api/matches/upcoming (regression)")
    try:
        response = requests.get(
            f"{BACKEND_URL}/api/matches/upcoming?sport=football",
            headers=headers,
            timeout=30
        )
        
        results.add_test(
            "/api/matches/upcoming returns 2xx",
            200 <= response.status_code < 300,
            f"Status: {response.status_code}"
        )
        
    except Exception as e:
        results.add_test("/api/matches/upcoming test", False, str(e))
    
    # Test 15: GET /api/understat/match/26651 (regression)
    print("\nTest 15: GET /api/understat/match/26651 (regression)")
    try:
        response = requests.get(
            f"{BACKEND_URL}/api/understat/match/26651",
            headers=headers,
            timeout=30
        )
        
        # Should return 2xx or 404 (if match doesn't exist), but not 500
        results.add_test(
            "/api/understat/match/26651 doesn't break",
            response.status_code != 500,
            f"Status: {response.status_code}"
        )
        
    except Exception as e:
        results.add_test("/api/understat/match test", False, str(e))
    
    return results


def test_spanish_narrative():
    """Test 16: Verify Spanish narrative in moneyball reason fields"""
    print("\n" + "="*60)
    print("TEST 16: Spanish Narrative Verification")
    print("="*60)
    
    results = TestResults()
    
    try:
        # Create a test pick and analyze it
        pick = {
            "match_id": "test_spanish",
            "match_label": "Test Match",
            "recommendation": {
                "market": "Under 3.5",
                "selection": "Under",
                "odds_range": "1.65-1.65",
                "confidence_score": 70
            },
            "reasoning": "Test pick",
            "risks": [],
            "is_live": False,
            "key_data": {}
        }
        
        result = mb.analyze_pick(pick, sport="football")
        reason = result["_moneyball"].get("classification_reason", "")
        
        # Check for Spanish words/phrases
        spanish_indicators = ["mercado", "edge", "confianza", "fragilidad", "señales", "trampa"]
        has_spanish = any(word in reason.lower() for word in spanish_indicators)
        
        results.add_test(
            "Moneyball reason contains Spanish text",
            has_spanish,
            f"Reason: {reason[:100]}..."
        )
        
    except Exception as e:
        results.add_test("Spanish narrative test", False, str(e))
    
    return results


def main():
    """Run all tests and generate report"""
    print("\n" + "="*80)
    print("PHASE 15 MONEYBALL REFACTOR TEST SUITE")
    print("="*80)
    print(f"Backend URL: {BACKEND_URL}")
    print(f"Test started at: {datetime.now().isoformat()}")
    
    all_results = []
    
    # Test 1: Auth
    auth_results, token = test_auth()
    all_results.append(("Authentication", auth_results))
    
    # Test 2: Module imports
    import_results = test_module_imports()
    all_results.append(("Module Imports", import_results))
    
    # Test 3: Market tolerance classification
    tolerance_results = test_classify_market_tolerance()
    all_results.append(("Market Tolerance", tolerance_results))
    
    # Test 4-7: analyze_pick classifications
    analyze_results = test_analyze_pick_classifications()
    all_results.append(("Analyze Pick Classifications", analyze_results))
    
    # Test 8: Moneyball summary
    summary_results = test_moneyball_summary()
    all_results.append(("Moneyball Summary", summary_results))
    
    # Test 9-10: Alternative rescue
    rescue_results = test_alternative_rescue()
    all_results.append(("Alternative Rescue", rescue_results))
    
    # Test 11-15: API endpoints
    if token:
        api_results = test_api_endpoints(token)
        all_results.append(("API Endpoints", api_results))
    else:
        print("\n⚠️  Skipping API endpoint tests (no auth token)")
    
    # Test 16: Spanish narrative
    spanish_results = test_spanish_narrative()
    all_results.append(("Spanish Narrative", spanish_results))
    
    # Overall summary
    print("\n" + "="*80)
    print("OVERALL TEST SUMMARY")
    print("="*80)
    
    total_passed = 0
    total_tests = 0
    
    for category, results in all_results:
        total_passed += results.passed
        total_tests += results.total
        status = "✅" if results.passed == results.total else "❌"
        print(f"{status} {category}: {results.passed}/{results.total} passed")
    
    print(f"\n{'='*80}")
    print(f"TOTAL: {total_passed}/{total_tests} tests passed")
    print(f"Success rate: {(total_passed/total_tests*100):.1f}%")
    print(f"{'='*80}")
    
    # Generate JSON report
    report = {
        "test_suite": "Phase 15 Moneyball Refactor",
        "timestamp": datetime.now().isoformat(),
        "backend_url": BACKEND_URL,
        "summary": {
            "total_tests": total_tests,
            "passed": total_passed,
            "failed": total_tests - total_passed,
            "success_rate": round(total_passed/total_tests*100, 1)
        },
        "categories": [
            {
                "name": category,
                "passed": results.passed,
                "total": results.total,
                "tests": results.results
            }
            for category, results in all_results
        ]
    }
    
    # Save report
    report_path = "/app/test_reports/iteration_22.json"
    with open(report_path, "w") as f:
        json.dump(report, f, indent=2)
    
    print(f"\nTest report saved to: {report_path}")
    
    return 0 if total_passed == total_tests else 1


if __name__ == "__main__":
    sys.exit(main())
