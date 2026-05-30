"""
MLB Engine V3 (V7) — Final Comprehensive Backend Test

Tests all requirements from the MLB-V7 review_request using real MLB data.
"""
import sys
import requests
import json
from datetime import datetime

BASE_URL = "https://low-volatility-plays.preview.emergentagent.com"

class Colors:
    GREEN = '\033[92m'
    RED = '\033[91m'
    YELLOW = '\033[93m'
    BLUE = '\033[94m'
    END = '\033[0m'

def log(message: str, level: str = "INFO"):
    prefix = {
        "INFO": f"{Colors.BLUE}ℹ{Colors.END}",
        "PASS": f"{Colors.GREEN}✓{Colors.END}",
        "FAIL": f"{Colors.RED}✗{Colors.END}",
        "WARN": f"{Colors.YELLOW}⚠{Colors.END}",
    }.get(level, "")
    print(f"{prefix} {message}")

def main():
    results = []
    tests_run = 0
    tests_passed = 0
    
    print(f"\n{Colors.BLUE}{'='*70}{Colors.END}")
    print(f"{Colors.BLUE}MLB ENGINE V3 (V7) — FINAL COMPREHENSIVE TEST{Colors.END}")
    print(f"{Colors.BLUE}{'='*70}{Colors.END}\n")
    
    # Login
    log("Authenticating...", "INFO")
    try:
        resp = requests.post(
            f"{BASE_URL}/api/auth/login",
            json={"email": "demo@valuebet.app", "password": "demo1234"},
            timeout=10
        )
        if resp.status_code != 200:
            log(f"Login failed: {resp.status_code}", "FAIL")
            return 1
        token = resp.json()["token"]
        log(f"Login successful", "PASS")
    except Exception as e:
        log(f"Login failed: {e}", "FAIL")
        return 1
    
    # Get MLB data
    log("\nFetching MLB data from /api/mlb/day...", "INFO")
    try:
        resp = requests.get(
            f"{BASE_URL}/api/mlb/day",
            headers={"Authorization": f"Bearer {token}"},
            timeout=60
        )
        if resp.status_code != 200:
            log(f"MLB request failed: {resp.status_code}", "FAIL")
            return 1
        data = resp.json()
        log(f"MLB data fetched successfully", "PASS")
    except Exception as e:
        log(f"MLB request failed: {e}", "FAIL")
        return 1
    
    # Check buckets
    log("\n" + "="*70, "INFO")
    log("TEST 1: MLB Buckets Preserved", "INFO")
    log("="*70, "INFO")
    tests_run += 1
    
    expected_buckets = [
        "picks", "rescued_picks", "structural_lean_requires_odds",
        "watchlist_manual_odds", "discarded_after_full_analysis"
    ]
    
    all_buckets_present = True
    for bucket in expected_buckets:
        if bucket not in data:
            log(f"Missing bucket: {bucket}", "FAIL")
            all_buckets_present = False
        else:
            log(f"Found bucket: {bucket} ({len(data[bucket])} items)", "PASS")
    
    if all_buckets_present:
        tests_passed += 1
        results.append({"test": "MLB buckets preserved", "status": "PASS"})
    else:
        results.append({"test": "MLB buckets preserved", "status": "FAIL"})
    
    # Get all picks
    picks = data.get("picks", []) + data.get("rescued_picks", [])
    structural = data.get("structural_lean_requires_odds", [])
    watchlist = data.get("watchlist_manual_odds", [])
    all_picks = picks + structural + watchlist
    
    log(f"\nTotal picks to test: {len(all_picks)}", "INFO")
    
    if not all_picks:
        log("No picks available for testing (may be valid if no games today)", "WARN")
        log(f"Schedule games found: {data.get('pipeline_meta', {}).get('schedule_games_found', 0)}", "INFO")
        log(f"Confirmed games: {data.get('pipeline_meta', {}).get('confirmed_games', 0)}", "INFO")
        
        # Save results
        report = {
            "test_suite": "MLB Engine V3 (V7) Final Test",
            "timestamp": datetime.now().isoformat(),
            "total_tests": tests_run,
            "passed": tests_passed,
            "failed": tests_run - tests_passed,
            "success_rate": f"{(tests_passed/tests_run*100):.1f}%" if tests_run > 0 else "0%",
            "results": results,
            "note": "No picks available for detailed testing"
        }
        
        with open("/app/test_reports/iteration_41.json", "w") as f:
            json.dump(report, f, indent=2)
        
        log(f"\nTest report saved to: /app/test_reports/iteration_41.json", "INFO")
        return 0
    
    # Test first pick in detail
    pick = all_picks[0]
    log(f"\nTesting pick: {pick.get('match_label', 'Unknown')}", "INFO")
    
    # TEST 2: _mlb_script_v3 structure
    log("\n" + "="*70, "INFO")
    log("TEST 2: _mlb_script_v3 Structure", "INFO")
    log("="*70, "INFO")
    tests_run += 1
    
    if "_mlb_script_v3" not in pick:
        log("FAIL: _mlb_script_v3 not found in pick", "FAIL")
        results.append({"test": "_mlb_script_v3 structure", "status": "FAIL", "error": "Field not found"})
    else:
        v3 = pick["_mlb_script_v3"]
        required_fields = ["script", "pitchers_block", "why_this_pick", "confidence_breakdown", "baseball_reasons"]
        
        all_fields_present = True
        for field in required_fields:
            if field not in v3:
                log(f"Missing field: {field}", "FAIL")
                all_fields_present = False
            else:
                log(f"Found field: {field}", "PASS")
        
        if all_fields_present:
            tests_passed += 1
            results.append({"test": "_mlb_script_v3 structure", "status": "PASS"})
        else:
            results.append({"test": "_mlb_script_v3 structure", "status": "FAIL"})
    
    # TEST 3: Script code validity
    log("\n" + "="*70, "INFO")
    log("TEST 3: Script Code Validity", "INFO")
    log("="*70, "INFO")
    tests_run += 1
    
    valid_codes = [
        "LOW_SCORING_PITCHERS_DUEL", "OFFENSIVE_SHOOTOUT", "FAVORITE_DOMINANCE",
        "BULLPEN_BATTLE", "UNDERDOG_CAN_COMPETE", "PITCHER_MISMATCH",
        "HIGH_VARIANCE_GAME", "LOW_VARIANCE_GAME"
    ]
    
    v3 = pick.get("_mlb_script_v3", {})
    script = v3.get("script", {})
    code = script.get("script_code")
    
    if code in valid_codes:
        log(f"Valid script_code: {code}", "PASS")
        log(f"Label: {script.get('label_es')}", "INFO")
        log(f"Narrative: {script.get('narrative_es', '')[:80]}...", "INFO")
        tests_passed += 1
        results.append({"test": "Script code validity", "status": "PASS", "code": code})
    else:
        log(f"Invalid script_code: {code}", "FAIL")
        results.append({"test": "Script code validity", "status": "FAIL", "code": code})
    
    # TEST 4: Pitchers block structure
    log("\n" + "="*70, "INFO")
    log("TEST 4: Pitchers Block Structure", "INFO")
    log("="*70, "INFO")
    tests_run += 1
    
    pitchers = v3.get("pitchers_block", {})
    home = pitchers.get("home", {})
    away = pitchers.get("away", {})
    
    pitcher_test_passed = True
    
    if not home.get("name"):
        log("Missing home pitcher name", "FAIL")
        pitcher_test_passed = False
    else:
        log(f"Home pitcher: {home['name']} (Q={home.get('qualityScore')})", "PASS")
    
    if "qualityScore" not in home:
        log("Missing home qualityScore", "FAIL")
        pitcher_test_passed = False
    
    if not isinstance(home.get("primary_stats"), list):
        log("Missing or invalid home primary_stats", "FAIL")
        pitcher_test_passed = False
    elif len(home["primary_stats"]) < 1 or len(home["primary_stats"]) > 2:
        log(f"Expected 1-2 primary_stats, got {len(home['primary_stats'])}", "FAIL")
        pitcher_test_passed = False
    else:
        log(f"Home stats: {home['primary_stats']}", "PASS")
    
    if not away.get("name"):
        log("Missing away pitcher name", "FAIL")
        pitcher_test_passed = False
    else:
        log(f"Away pitcher: {away['name']} (Q={away.get('qualityScore')})", "PASS")
    
    if "qualityScore" not in away:
        log("Missing away qualityScore", "FAIL")
        pitcher_test_passed = False
    
    if pitcher_test_passed:
        tests_passed += 1
        results.append({"test": "Pitchers block structure", "status": "PASS"})
    else:
        results.append({"test": "Pitchers block structure", "status": "FAIL"})
    
    # TEST 5: Pitcher quality matches scoring_ctx
    log("\n" + "="*70, "INFO")
    log("TEST 5: Pitcher Quality Matches scoring_ctx", "INFO")
    log("="*70, "INFO")
    tests_run += 1
    
    all_comp = pick.get("all_components", {})
    home_q_ctx = all_comp.get("home_pitcher_quality", {}).get("score")
    away_q_ctx = all_comp.get("away_pitcher_quality", {}).get("score")
    home_q_v3 = home.get("qualityScore")
    away_q_v3 = away.get("qualityScore")
    
    quality_match = True
    if home_q_ctx is not None and home_q_v3 is not None:
        if abs(home_q_ctx - home_q_v3) > 1:
            log(f"Home quality mismatch: ctx={home_q_ctx} v3={home_q_v3}", "FAIL")
            quality_match = False
        else:
            log(f"Home quality matches: {home_q_v3}", "PASS")
    
    if away_q_ctx is not None and away_q_v3 is not None:
        if abs(away_q_ctx - away_q_v3) > 1:
            log(f"Away quality mismatch: ctx={away_q_ctx} v3={away_q_v3}", "FAIL")
            quality_match = False
        else:
            log(f"Away quality matches: {away_q_v3}", "PASS")
    
    if quality_match:
        tests_passed += 1
        results.append({"test": "Pitcher quality matches", "status": "PASS"})
    else:
        results.append({"test": "Pitcher quality matches", "status": "FAIL"})
    
    # TEST 6: Why this pick rows
    log("\n" + "="*70, "INFO")
    log("TEST 6: Why This Pick Rows", "INFO")
    log("="*70, "INFO")
    tests_run += 1
    
    why = v3.get("why_this_pick", [])
    if len(why) >= 5:
        log(f"Found {len(why)} why_this_pick rows (>= 5)", "PASS")
        for i, row in enumerate(why[:3], 1):
            log(f"  Row {i}: {row.get('label')}: {row.get('value')}", "INFO")
        tests_passed += 1
        results.append({"test": "Why this pick rows", "status": "PASS", "count": len(why)})
    else:
        log(f"Expected >= 5 rows, got {len(why)}", "FAIL")
        results.append({"test": "Why this pick rows", "status": "FAIL", "count": len(why)})
    
    # TEST 7: Confidence breakdown components
    log("\n" + "="*70, "INFO")
    log("TEST 7: Confidence Breakdown Components", "INFO")
    log("="*70, "INFO")
    tests_run += 1
    
    breakdown = v3.get("confidence_breakdown", {})
    components = breakdown.get("components", [])
    
    if len(components) == 5:
        log(f"Found 5 confidence components", "PASS")
        expected_keys = ["pitchers", "lineups", "bullpens", "park", "historical"]
        all_keys_valid = True
        for comp in components:
            if comp.get("key") not in expected_keys:
                log(f"Unexpected component key: {comp.get('key')}", "FAIL")
                all_keys_valid = False
            else:
                log(f"  {comp.get('label')}: {comp.get('value')} ({comp.get('weight')}%)", "INFO")
        
        if all_keys_valid:
            tests_passed += 1
            results.append({"test": "Confidence breakdown components", "status": "PASS"})
        else:
            results.append({"test": "Confidence breakdown components", "status": "FAIL"})
    else:
        log(f"Expected 5 components, got {len(components)}", "FAIL")
        results.append({"test": "Confidence breakdown components", "status": "FAIL"})
    
    # TEST 8: Confidence total matches score
    log("\n" + "="*70, "INFO")
    log("TEST 8: Confidence Total Matches Score", "INFO")
    log("="*70, "INFO")
    tests_run += 1
    
    total = breakdown.get("total")
    rec = pick.get("recommendation", {})
    score = rec.get("score")
    
    if total is not None and score is not None:
        if abs(total - score) <= 1:
            log(f"Confidence total {total} matches score {score} (within ±1)", "PASS")
            tests_passed += 1
            results.append({"test": "Confidence total matches score", "status": "PASS"})
        else:
            log(f"Total {total} doesn't match score {score}", "FAIL")
            results.append({"test": "Confidence total matches score", "status": "FAIL"})
    else:
        log("Missing total or score", "WARN")
        results.append({"test": "Confidence total matches score", "status": "SKIP"})
    
    # TEST 9: Baseball reasons no generic phrases
    log("\n" + "="*70, "INFO")
    log("TEST 9: Baseball Reasons (No Generic Phrases)", "INFO")
    log("="*70, "INFO")
    tests_run += 1
    
    reasons = v3.get("baseball_reasons", [])
    
    if len(reasons) < 1:
        log("Expected at least 1 baseball reason", "FAIL")
        results.append({"test": "Baseball reasons no generic", "status": "FAIL"})
    else:
        generic_phrases = [
            "Lectura estructural detectada",
            "Mercado rescatado",
            "Línea óptima seleccionada",
        ]
        
        all_text = " ".join(reasons)
        has_generic = False
        for phrase in generic_phrases:
            if phrase in all_text:
                log(f"Found generic phrase: {phrase}", "FAIL")
                has_generic = True
        
        baseball_terms = [
            "abridor", "pitcher", "carreras", "parque", "bullpen",
            "lineup", "ops", "under", "over", "run line", "calidad",
            "ofensiva", "pitching", "margen", "proyect"
        ]
        all_text_lower = all_text.lower()
        found_terms = [term for term in baseball_terms if term in all_text_lower]
        
        if not has_generic and len(found_terms) >= 2:
            log(f"Found {len(reasons)} baseball-first reasons", "PASS")
            for i, reason in enumerate(reasons, 1):
                log(f"  {i}. {reason[:80]}...", "INFO")
            log(f"Baseball terms found: {found_terms[:5]}", "PASS")
            tests_passed += 1
            results.append({"test": "Baseball reasons no generic", "status": "PASS"})
        else:
            if has_generic:
                log("Contains generic phrases", "FAIL")
            if len(found_terms) < 2:
                log(f"Not enough baseball terms: {found_terms}", "FAIL")
            results.append({"test": "Baseball reasons no generic", "status": "FAIL"})
    
    # TEST 10: Diversity metadata present
    log("\n" + "="*70, "INFO")
    log("TEST 10: Diversity Metadata Present", "INFO")
    log("="*70, "INFO")
    tests_run += 1
    
    if "_mlb_script_v3_diversity" not in pick:
        log("Missing _mlb_script_v3_diversity", "FAIL")
        results.append({"test": "Diversity metadata present", "status": "FAIL"})
    else:
        div = pick["_mlb_script_v3_diversity"]
        required_keys = ["dominant_market", "dominant_share", "is_dominant", 
                        "alt_suggestions", "diversity_penalty"]
        
        all_keys_present = True
        for key in required_keys:
            if key not in div:
                log(f"Missing diversity key: {key}", "FAIL")
                all_keys_present = False
        
        if all_keys_present:
            log(f"Dominant market: {div['dominant_market']}", "INFO")
            log(f"Dominant share: {div['dominant_share']}", "INFO")
            log(f"Is dominant: {div['is_dominant']}", "INFO")
            log(f"Diversity penalty: {div['diversity_penalty']}", "INFO")
            tests_passed += 1
            results.append({"test": "Diversity metadata present", "status": "PASS"})
        else:
            results.append({"test": "Diversity metadata present", "status": "FAIL"})
    
    # TEST 11: Diversity penalty logic
    log("\n" + "="*70, "INFO")
    log("TEST 11: Diversity Penalty Logic", "INFO")
    log("="*70, "INFO")
    tests_run += 1
    
    if len(all_picks) >= 3:
        penalty_correct = True
        for p in all_picks:
            div = p.get("_mlb_script_v3_diversity", {})
            if div.get("is_dominant") and div.get("dominant_share", 0) >= 0.60:
                penalty = div.get("diversity_penalty", 0)
                if penalty < 6:
                    log(f"Expected penalty >= 6, got {penalty}", "FAIL")
                    penalty_correct = False
                else:
                    log(f"Dominant pick has penalty {penalty} (>= 6)", "PASS")
        
        if penalty_correct:
            tests_passed += 1
            results.append({"test": "Diversity penalty logic", "status": "PASS"})
        else:
            results.append({"test": "Diversity penalty logic", "status": "FAIL"})
    else:
        log(f"Less than 3 picks ({len(all_picks)}), dominance not applicable", "WARN")
        results.append({"test": "Diversity penalty logic", "status": "SKIP"})
    
    # TEST 12: Football regression
    log("\n" + "="*70, "INFO")
    log("TEST 12: Football Pipeline (No MLB V3 Regression)", "INFO")
    log("="*70, "INFO")
    tests_run += 1
    
    try:
        resp = requests.post(
            f"{BASE_URL}/api/analysis/run",
            json={"sport": "football", "background": False, "max_matches": 2},
            headers={"Authorization": f"Bearer {token}"},
            timeout=60
        )
        
        if resp.status_code == 200:
            fb_data = resp.json()
            # Check if it's a job response or direct response
            if "picks" in fb_data:
                fb_picks = fb_data.get("picks", [])
                if fb_picks:
                    fb_pick = fb_picks[0]
                    if "_mlb_script_v3" in fb_pick or "baseball_reasons" in fb_pick:
                        log("Found MLB V3 fields in football pick (REGRESSION)", "FAIL")
                        results.append({"test": "Football no MLB V3 regression", "status": "FAIL"})
                    else:
                        log("Football pipeline clean (no MLB V3 fields)", "PASS")
                        tests_passed += 1
                        results.append({"test": "Football no MLB V3 regression", "status": "PASS"})
                else:
                    log("No football picks (may be valid)", "WARN")
                    results.append({"test": "Football no MLB V3 regression", "status": "SKIP"})
            else:
                log("Football request returned job (background mode)", "WARN")
                results.append({"test": "Football no MLB V3 regression", "status": "SKIP"})
        else:
            log(f"Football request failed: {resp.status_code}", "WARN")
            results.append({"test": "Football no MLB V3 regression", "status": "SKIP"})
    except Exception as e:
        log(f"Football test error: {e}", "WARN")
        results.append({"test": "Football no MLB V3 regression", "status": "SKIP"})
    
    # Summary
    log("\n" + "="*70, "INFO")
    log("TEST SUMMARY", "INFO")
    log("="*70, "INFO")
    log(f"Total: {tests_run}", "INFO")
    log(f"Passed: {tests_passed}", "PASS")
    log(f"Failed: {tests_run - tests_passed}", "FAIL" if tests_run - tests_passed > 0 else "INFO")
    log(f"Success Rate: {(tests_passed/tests_run*100):.1f}%", "INFO")
    
    # Save results
    report = {
        "test_suite": "MLB Engine V3 (V7) Final Test",
        "timestamp": datetime.now().isoformat(),
        "total_tests": tests_run,
        "passed": tests_passed,
        "failed": tests_run - tests_passed,
        "success_rate": f"{(tests_passed/tests_run*100):.1f}%",
        "results": results,
        "sample_pick": {
            "match": pick.get("match_label"),
            "classification": pick.get("classification"),
            "script_code": v3.get("script", {}).get("script_code"),
            "pitchers": {
                "home": home.get("name"),
                "away": away.get("name"),
            },
            "diversity": {
                "dominant_market": div.get("dominant_market"),
                "is_dominant": div.get("is_dominant"),
            }
        }
    }
    
    with open("/app/test_reports/iteration_41.json", "w") as f:
        json.dump(report, f, indent=2)
    
    log(f"\nTest report saved to: /app/test_reports/iteration_41.json", "INFO")
    
    return 0 if tests_passed == tests_run else 1

if __name__ == "__main__":
    sys.exit(main())
