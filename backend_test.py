#!/usr/bin/env python3
"""Backend Testing for MLB Intelligence Warehouse Integration (Fix 1-3).

Tests:
1. Backend boot OK
2. Login endpoint works
3. POST /api/analysis/run with sport=baseball produces picks with Phase 13 fields
4. MLB prompt contains new keywords and NO obsolete rules
5. Prefilter prompt doesn't DISCARD for normal motivation or missing odds
6. mlb_game_intelligence_snapshots collection populating
7. Fail-soft validation
8. Sample-size gates
9. Football/basketball not affected
"""

import sys
import requests
import json
from datetime import datetime

BASE_URL = "https://low-volatility-plays.preview.emergentagent.com"
MONGO_URL = "mongodb://localhost:27017"
DB_NAME = "test_database"

class Colors:
    GREEN = '\033[92m'
    RED = '\033[91m'
    YELLOW = '\033[93m'
    BLUE = '\033[94m'
    END = '\033[0m'

def print_test(name, passed, details=""):
    status = f"{Colors.GREEN}✅ PASS{Colors.END}" if passed else f"{Colors.RED}❌ FAIL{Colors.END}"
    print(f"{status} - {name}")
    if details:
        print(f"    {details}")
    return passed

def test_login():
    """Test 1: Login endpoint works"""
    try:
        response = requests.post(
            f"{BASE_URL}/api/auth/login",
            json={"email": "demo@valuebet.app", "password": "demo1234"},
            timeout=10
        )
        if response.status_code == 200:
            data = response.json()
            token = data.get("token")
            return print_test("Login endpoint", token is not None, f"Token received: {token[:50] if token else 'None'}...")
        else:
            return print_test("Login endpoint", False, f"Status: {response.status_code}")
    except Exception as e:
        return print_test("Login endpoint", False, f"Error: {str(e)}")

def get_auth_token():
    """Helper to get auth token"""
    response = requests.post(
        f"{BASE_URL}/api/auth/login",
        json={"email": "demo@valuebet.app", "password": "demo1234"},
        timeout=10
    )
    return response.json().get("token")

def test_mlb_prompt_content():
    """Test 2: MLB prompt contains new keywords and NO obsolete rules"""
    try:
        from services.analyst_engine import _build_system_prompt
        from services.mlb_intelligence import MLB_INTELLIGENCE_RULES
        
        prompt = _build_system_prompt("baseball")
        
        # Check for NEW keywords
        new_keywords = [
            "pressure_base", "market_selection", "sabermetrics", 
            "ghost-edges", "historical_pattern_match", "STRUCTURAL_LEAN"
        ]
        has_new = all(kw in prompt for kw in new_keywords)
        
        # Check for OBSOLETE rules (should NOT be present)
        obsolete_phrases = [
            "Run Line -1.5 del favorito prohibido como principal",
            "Total Runs UNDER solo cuando ambos pitchers son de élite"
        ]
        has_obsolete = any(phrase in prompt for phrase in obsolete_phrases)
        
        # Check MLB_INTELLIGENCE_RULES
        mlb_rules_ok = "PIPELINE ACTUALIZADO" in MLB_INTELLIGENCE_RULES
        mlb_rules_ok = mlb_rules_ok and "advanced_stats_snapshot" in MLB_INTELLIGENCE_RULES
        mlb_rules_ok = mlb_rules_ok and "pressure_base" in MLB_INTELLIGENCE_RULES
        
        passed = has_new and not has_obsolete and mlb_rules_ok
        details = f"New keywords: {has_new}, No obsolete: {not has_obsolete}, MLB rules OK: {mlb_rules_ok}"
        return print_test("MLB prompt content", passed, details)
    except Exception as e:
        return print_test("MLB prompt content", False, f"Error: {str(e)}")

def test_prefilter_prompt():
    """Test 3: Prefilter prompt doesn't DISCARD for normal motivation or missing odds in MLB"""
    try:
        from services.analyst_engine import _build_prefilter_prompt
        
        prefilter = _build_prefilter_prompt("baseball")
        
        # Check for MLB-specific rules
        has_mlb_rules = "PARA SPORT=baseball ÚNICAMENTE" in prefilter
        no_discard_normal = "motivación normal es NEUTRAL" in prefilter or "NUNCA es razón de DISCARD" in prefilter
        no_discard_odds = "Cuotas ausentes en MLB NUNCA implican DISCARD" in prefilter
        
        passed = has_mlb_rules and no_discard_normal and no_discard_odds
        details = f"MLB rules: {has_mlb_rules}, No discard normal: {no_discard_normal}, No discard odds: {no_discard_odds}"
        return print_test("Prefilter prompt MLB rules", passed, details)
    except Exception as e:
        return print_test("Prefilter prompt MLB rules", False, f"Error: {str(e)}")

def test_warehouse_collections():
    """Test 4: mlb_game_intelligence_snapshots collection exists and has data"""
    try:
        from pymongo import MongoClient
        
        client = MongoClient(MONGO_URL)
        db = client[DB_NAME]
        
        # Check collection exists and has documents
        count = db.mlb_game_intelligence_snapshots.count_documents({})
        
        if count > 0:
            # Check one document structure
            doc = db.mlb_game_intelligence_snapshots.find_one({})
            has_required_fields = all(k in doc for k in ["game_pk", "day", "digest", "pattern_keys"])
            
            # Check digest has Phase 13 fields
            digest = doc.get("digest", {})
            phase13_fields = [
                "advanced_stats_snapshot", "advanced_adjustments", "pressure_base",
                "pressure_base_impact", "sabermetrics", "sabermetrics_audit",
                "market_selection"
            ]
            has_phase13 = all(f in digest for f in phase13_fields)
            
            passed = has_required_fields and has_phase13
            details = f"Documents: {count}, Required fields: {has_required_fields}, Phase 13 fields: {has_phase13}"
            return print_test("Warehouse collection populated", passed, details)
        else:
            return print_test("Warehouse collection populated", False, f"No documents found (count: {count})")
    except Exception as e:
        return print_test("Warehouse collection populated", False, f"Error: {str(e)}")

def test_fail_soft():
    """Test 5: Fail-soft behavior when db is None"""
    try:
        from services.mlb_intelligence_warehouse import (
            lookup_pattern_match, attach_pattern_match_to_payload
        )
        import asyncio
        
        async def run_test():
            # Test lookup with None db
            result = await lookup_pattern_match(None, ["LOW_PRESSURE_STRONG_FIP_BOTH"])
            lookup_ok = result["sample_size"] == 0 and result["confidence_adjustment"] == 0.0
            
            # Test attach with None db
            payload = {"pressure_base": {"combined": {"pressure_tier": "LOW_PRESSURE"}}}
            summary = await attach_pattern_match_to_payload(None, payload)
            attach_ok = summary["confidence_adjustment"] == 0.0
            attach_ok = attach_ok and "historical_pattern_match" in payload
            
            return lookup_ok and attach_ok
        
        passed = asyncio.run(run_test())
        return print_test("Fail-soft behavior", passed, "db=None handled gracefully")
    except Exception as e:
        return print_test("Fail-soft behavior", False, f"Error: {str(e)}")

def test_sample_size_gates():
    """Test 6: Sample-size gates working correctly"""
    try:
        from services.mlb_intelligence_warehouse import _compute_pattern_adjustment
        
        # Test <20: no adjustment
        adj1, codes1, warn1 = _compute_pattern_adjustment(sample_size=10, hit_rate=0.8, roi=0.3)
        test1 = adj1 == 0.0 and warn1 is not None
        
        # Test 20-49: moderate adjustment capped at ±5
        adj2, codes2, _ = _compute_pattern_adjustment(sample_size=30, hit_rate=0.62, roi=0.10)
        test2 = -5.0 <= adj2 <= 5.0
        
        # Test >=50 with positive ROI: strong adjustment up to ±8
        adj3, codes3, _ = _compute_pattern_adjustment(sample_size=60, hit_rate=0.62, roi=0.15)
        test3 = 0 < adj3 <= 8.0
        
        # Test >=50 with negative ROI: negative adjustment
        adj4, codes4, _ = _compute_pattern_adjustment(sample_size=60, hit_rate=0.4, roi=-0.10)
        test4 = adj4 < 0
        
        passed = test1 and test2 and test3 and test4
        details = f"<20: {test1}, 20-49: {test2}, >=50 pos: {test3}, >=50 neg: {test4}"
        return print_test("Sample-size gates", passed, details)
    except Exception as e:
        return print_test("Sample-size gates", False, f"Error: {str(e)}")

def test_football_basketball_unaffected():
    """Test 7: Football/basketball prompts not affected by MLB changes"""
    try:
        from services.analyst_engine import _build_system_prompt
        
        football_prompt = _build_system_prompt("football")
        basketball_prompt = _build_system_prompt("basketball")
        
        # These should NOT contain MLB-specific content
        football_ok = "pressure_base" not in football_prompt
        football_ok = football_ok and "sabermetrics" not in football_prompt
        football_ok = football_ok and "REGLAS DEL DEPORTE (Fútbol)" in football_prompt
        
        basketball_ok = "pressure_base" not in basketball_prompt
        basketball_ok = basketball_ok and "sabermetrics" not in basketball_prompt
        basketball_ok = basketball_ok and "REGLAS DEL DEPORTE (NBA/Basket)" in basketball_prompt
        
        passed = football_ok and basketball_ok
        details = f"Football clean: {football_ok}, Basketball clean: {basketball_ok}"
        return print_test("Football/Basketball unaffected", passed, details)
    except Exception as e:
        return print_test("Football/Basketball unaffected", False, f"Error: {str(e)}")

def test_pytest_suite():
    """Test 8: Full pytest suite passes (701 tests)"""
    try:
        import subprocess
        result = subprocess.run(
            ["python", "-m", "pytest", "tests/", "-q", "--tb=no"],
            cwd="/app/backend",
            capture_output=True,
            text=True,
            timeout=120
        )
        
        # Parse output for test count
        output = result.stdout
        if "701 passed" in output:
            return print_test("Pytest suite (701 tests)", True, "All tests passed")
        else:
            # Extract actual count
            import re
            match = re.search(r"(\d+) passed", output)
            count = match.group(1) if match else "unknown"
            return print_test("Pytest suite (701 tests)", False, f"Expected 701, got {count}")
    except Exception as e:
        return print_test("Pytest suite (701 tests)", False, f"Error: {str(e)}")

def main():
    print(f"\n{Colors.BLUE}{'='*70}{Colors.END}")
    print(f"{Colors.BLUE}MLB Intelligence Warehouse Integration Test Suite{Colors.END}")
    print(f"{Colors.BLUE}Testing Fixes 1-3: Prompt Updates + Warehouse + Pick Regeneration{Colors.END}")
    print(f"{Colors.BLUE}{'='*70}{Colors.END}\n")
    
    results = []
    
    # Run tests
    print(f"{Colors.YELLOW}Running Backend Tests...{Colors.END}\n")
    
    results.append(test_login())
    results.append(test_mlb_prompt_content())
    results.append(test_prefilter_prompt())
    results.append(test_warehouse_collections())
    results.append(test_fail_soft())
    results.append(test_sample_size_gates())
    results.append(test_football_basketball_unaffected())
    results.append(test_pytest_suite())
    
    # Summary
    passed = sum(results)
    total = len(results)
    
    print(f"\n{Colors.BLUE}{'='*70}{Colors.END}")
    print(f"{Colors.BLUE}Test Summary{Colors.END}")
    print(f"{Colors.BLUE}{'='*70}{Colors.END}")
    print(f"Total: {total} tests")
    print(f"{Colors.GREEN}Passed: {passed}{Colors.END}")
    print(f"{Colors.RED}Failed: {total - passed}{Colors.END}")
    print(f"Success Rate: {passed/total*100:.1f}%\n")
    
    return 0 if passed == total else 1

if __name__ == "__main__":
    sys.exit(main())
