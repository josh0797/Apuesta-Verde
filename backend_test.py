"""
Backend Test Suite for MLB Under Veto Layer + Live Baseball Integration
========================================================================

Tests:
  TASK A — MLB Under Veto Layer
  1. derive_pitcher_quality_score() with various ERA/WHIP combinations
  2. build_under_veto_context() mapping from profile.pitching
  3. evaluate_under_veto() rules and severity logic
  4. /api/mlb/day endpoint veto integration
  
  TASK B — MLB Live Section
  5. /api/matches/live?sport=baseball endpoint returns proper data
"""

import sys
import requests
from datetime import datetime

# Backend URL from environment
BACKEND_URL = "https://low-volatility-plays.preview.emergentagent.com"

class TestRunner:
    def __init__(self):
        self.tests_run = 0
        self.tests_passed = 0
        self.tests_failed = 0
        self.failures = []

    def test(self, name, condition, details=""):
        """Run a single test assertion"""
        self.tests_run += 1
        print(f"\n🔍 Test {self.tests_run}: {name}")
        if condition:
            self.tests_passed += 1
            print(f"✅ PASSED")
            if details:
                print(f"   {details}")
            return True
        else:
            self.tests_failed += 1
            print(f"❌ FAILED")
            if details:
                print(f"   {details}")
            self.failures.append({"test": name, "details": details})
            return False

    def summary(self):
        """Print test summary"""
        print("\n" + "="*70)
        print(f"📊 TEST SUMMARY")
        print("="*70)
        print(f"Total tests: {self.tests_run}")
        print(f"✅ Passed: {self.tests_passed}")
        print(f"❌ Failed: {self.tests_failed}")
        print(f"Success rate: {(self.tests_passed/self.tests_run*100):.1f}%")
        
        if self.failures:
            print("\n❌ FAILED TESTS:")
            for i, f in enumerate(self.failures, 1):
                print(f"{i}. {f['test']}")
                if f['details']:
                    print(f"   {f['details']}")
        
        return self.tests_failed == 0


def test_veto_layer_functions():
    """Test Task A: MLB Under Veto Layer pure functions"""
    print("\n" + "="*70)
    print("TASK A — MLB UNDER VETO LAYER FUNCTIONS")
    print("="*70)
    
    runner = TestRunner()
    
    try:
        # Import the veto layer module
        sys.path.insert(0, '/app/backend')
        from services.mlb_under_veto_layer import (
            derive_pitcher_quality_score,
            build_under_veto_context,
            evaluate_under_veto,
        )
        
        # Test 1: derive_pitcher_quality_score with ace pitcher
        score_ace = derive_pitcher_quality_score(era=2.10, whip=0.95)
        runner.test(
            "derive_pitcher_quality_score(era=2.10, whip=0.95) returns score >= 0.85",
            score_ace >= 0.85,
            f"Got score: {score_ace}"
        )
        
        # Test 2: derive_pitcher_quality_score with weak pitcher
        score_weak = derive_pitcher_quality_score(era=5.50, whip=1.60)
        runner.test(
            "derive_pitcher_quality_score(era=5.50, whip=1.60) returns score <= 0.30",
            score_weak <= 0.30,
            f"Got score: {score_weak}"
        )
        
        # Test 3: derive_pitcher_quality_score with missing data
        score_missing = derive_pitcher_quality_score(era=None, whip=None)
        runner.test(
            "derive_pitcher_quality_score(era=None, whip=None) returns 0.0",
            score_missing == 0.0,
            f"Got score: {score_missing}"
        )
        
        # Test 4: build_under_veto_context mapping
        profile = {
            "pitching": {
                "homeStarter": {
                    "name": "Test Pitcher Home",
                    "era": 3.20,
                    "whip": 1.15,
                    "gamesStarted": 10,
                },
                "awayStarter": {
                    "name": "Test Pitcher Away",
                    "era": 4.10,
                    "whip": 1.35,
                    "starts": 8,
                },
                "homeBullpen": {
                    "era_7d": 4.50,
                },
                "awayBullpen": {
                    "era7d": 3.80,
                },
            },
            "park": {
                "runFactor": 1.15,
            },
            "combined": {
                "h2hTotalRunsAvg": 8.5,
            },
        }
        
        ctx = build_under_veto_context(profile)
        
        runner.test(
            "build_under_veto_context maps home_pitcher correctly",
            (ctx.get("home_pitcher", {}).get("era") == 3.20 and
             ctx.get("home_pitcher", {}).get("whip") == 1.15 and
             ctx.get("home_pitcher", {}).get("games_pitched") == 10),
            f"home_pitcher: {ctx.get('home_pitcher')}"
        )
        
        runner.test(
            "build_under_veto_context maps away_pitcher correctly",
            (ctx.get("away_pitcher", {}).get("era") == 4.10 and
             ctx.get("away_pitcher", {}).get("whip") == 1.35 and
             ctx.get("away_pitcher", {}).get("games_pitched") == 8),
            f"away_pitcher: {ctx.get('away_pitcher')}"
        )
        
        runner.test(
            "build_under_veto_context derives quality_score",
            (ctx.get("home_pitcher", {}).get("quality_score") > 0 and
             ctx.get("away_pitcher", {}).get("quality_score") > 0),
            f"home quality: {ctx.get('home_pitcher', {}).get('quality_score')}, away quality: {ctx.get('away_pitcher', {}).get('quality_score')}"
        )
        
        runner.test(
            "build_under_veto_context maps park_factor",
            ctx.get("park", {}).get("run_factor") == 1.15,
            f"park: {ctx.get('park')}"
        )
        
        runner.test(
            "build_under_veto_context maps recent_h2h_avg_runs",
            ctx.get("recent_h2h_avg_runs") == 8.5,
            f"recent_h2h_avg_runs: {ctx.get('recent_h2h_avg_runs')}"
        )
        
        runner.test(
            "build_under_veto_context does NOT invent xera",
            "xera" not in ctx.get("home_pitcher", {}) and "xera" not in ctx.get("away_pitcher", {}),
            f"_xera_available: {ctx.get('_xera_available')}"
        )
        
        # Test 5: evaluate_under_veto with insufficient sample
        veto_insufficient = evaluate_under_veto(
            pitcher_home={"games_pitched": 2, "quality_score": 0.7},
            pitcher_away={"games_pitched": 5, "quality_score": 0.6},
            park={"run_factor": 1.0},
            book_total=8.5,
            expected_runs=7.5,
        )
        
        runner.test(
            "evaluate_under_veto blocks when games_pitched < 3",
            (veto_insufficient.get("veto") == True and
             veto_insufficient.get("severity") == "BLOCKED" and
             "INSUFFICIENT_PITCHER_SAMPLE" in veto_insufficient.get("veto_reasons", [])),
            f"Result: {veto_insufficient}"
        )
        
        # Test 6: evaluate_under_veto with no pitcher data
        veto_no_data = evaluate_under_veto(
            pitcher_home={"games_pitched": 5, "quality_score": 0.0},
            pitcher_away={"games_pitched": 5, "quality_score": 0.0},
            park={"run_factor": 1.0},
            book_total=8.5,
            expected_runs=7.5,
        )
        
        runner.test(
            "evaluate_under_veto blocks when both quality_score == 0",
            (veto_no_data.get("veto") == True and
             veto_no_data.get("severity") == "BLOCKED" and
             "NO_PITCHER_DATA" in veto_no_data.get("veto_reasons", [])),
            f"Result: {veto_no_data}"
        )
        
        # Test 7: evaluate_under_veto with offensive park thin margin
        veto_park = evaluate_under_veto(
            pitcher_home={"games_pitched": 5, "quality_score": 0.6},
            pitcher_away={"games_pitched": 5, "quality_score": 0.6},
            park={"run_factor": 1.15},
            book_total=8.5,
            expected_runs=7.5,
        )
        
        runner.test(
            "evaluate_under_veto detects OFFENSIVE_PARK_THIN_MARGIN",
            "OFFENSIVE_PARK_THIN_MARGIN" in veto_park.get("veto_reasons", []),
            f"Reasons: {veto_park.get('veto_reasons')}, Severity: {veto_park.get('severity')}"
        )
        
        # Test 8: evaluate_under_veto with bullpen blowup risk
        veto_bullpen = evaluate_under_veto(
            pitcher_home={"games_pitched": 5, "quality_score": 0.7},
            pitcher_away={"games_pitched": 5, "quality_score": 0.7},
            park={"run_factor": 1.0},
            book_total=8.5,
            expected_runs=7.5,
            bullpen_home={"era_7d": 5.5},
            bullpen_away={"era_7d": 3.5},
        )
        
        runner.test(
            "evaluate_under_veto detects BULLPEN_BLOWUP_RISK",
            "BULLPEN_BLOWUP_RISK" in veto_bullpen.get("veto_reasons", []),
            f"Reasons: {veto_bullpen.get('veto_reasons')}"
        )
        
        # Test 9: evaluate_under_veto with recent over pattern
        veto_h2h = evaluate_under_veto(
            pitcher_home={"games_pitched": 5, "quality_score": 0.7},
            pitcher_away={"games_pitched": 5, "quality_score": 0.7},
            park={"run_factor": 1.0},
            book_total=8.5,
            expected_runs=7.5,
            recent_h2h_avg_runs=9.5,
        )
        
        runner.test(
            "evaluate_under_veto detects RECENT_OVER_PATTERN",
            "RECENT_OVER_PATTERN" in veto_h2h.get("veto_reasons", []),
            f"Reasons: {veto_h2h.get('veto_reasons')}"
        )
        
        # Test 10: evaluate_under_veto severity logic - WARNING with 2 reasons
        veto_warning = evaluate_under_veto(
            pitcher_home={"games_pitched": 5, "quality_score": 0.5},
            pitcher_away={"games_pitched": 5, "quality_score": 0.5},
            park={"run_factor": 1.15},
            book_total=8.5,
            expected_runs=7.5,
            bullpen_home={"era_7d": 5.5},
        )
        
        runner.test(
            "evaluate_under_veto returns WARNING with penalty when 2+ reasons",
            (veto_warning.get("severity") == "WARNING" and
             veto_warning.get("confidence_penalty") in [8, 15]),
            f"Severity: {veto_warning.get('severity')}, Penalty: {veto_warning.get('confidence_penalty')}, Reasons: {veto_warning.get('veto_reasons')}"
        )
        
        # Test 11: evaluate_under_veto PASS when no issues
        veto_pass = evaluate_under_veto(
            pitcher_home={"games_pitched": 10, "quality_score": 0.75},
            pitcher_away={"games_pitched": 10, "quality_score": 0.75},
            park={"run_factor": 1.0},
            book_total=8.5,
            expected_runs=6.5,
        )
        
        runner.test(
            "evaluate_under_veto returns PASS when no issues",
            (veto_pass.get("severity") == "PASS" and
             veto_pass.get("veto") == False and
             len(veto_pass.get("veto_reasons", [])) == 0),
            f"Result: {veto_pass}"
        )
        
    except Exception as e:
        print(f"\n❌ ERROR importing or testing veto layer: {e}")
        import traceback
        traceback.print_exc()
        return False
    
    return runner.summary()


def test_mlb_live_api():
    """Test Task B: /api/matches/live?sport=baseball endpoint"""
    print("\n" + "="*70)
    print("TASK B — MLB LIVE API ENDPOINT")
    print("="*70)
    
    runner = TestRunner()
    
    try:
        url = f"{BACKEND_URL}/api/matches/live"
        params = {"sport": "baseball", "_ts": int(datetime.now().timestamp())}
        
        print(f"\n📡 GET {url}?sport=baseball")
        response = requests.get(url, params=params, timeout=15)
        
        runner.test(
            "/api/matches/live?sport=baseball returns 200",
            response.status_code == 200,
            f"Status: {response.status_code}"
        )
        
        if response.status_code == 200:
            data = response.json()
            items = data.get("items", [])
            
            runner.test(
                "/api/matches/live returns items array",
                isinstance(items, list),
                f"Got {len(items)} items"
            )
            
            runner.test(
                "/api/matches/live returns at least 1 baseball game",
                len(items) >= 1,
                f"Found {len(items)} games"
            )
            
            if len(items) > 0:
                # Test first item structure
                item = items[0]
                
                runner.test(
                    "Live item has live_stats",
                    "live_stats" in item,
                    f"Keys: {list(item.keys())}"
                )
                
                if "live_stats" in item:
                    live_stats = item["live_stats"]
                    
                    runner.test(
                        "live_stats has score",
                        "score" in live_stats,
                        f"live_stats keys: {list(live_stats.keys())}"
                    )
                    
                    runner.test(
                        "live_stats has inning",
                        "inning" in live_stats,
                        f"inning: {live_stats.get('inning')}"
                    )
                    
                    runner.test(
                        "live_stats has inning_half",
                        "inning_half" in live_stats,
                        f"inning_half: {live_stats.get('inning_half')}"
                    )
                    
                    runner.test(
                        "live_stats has home_stats with Hits/Errors/Runs",
                        ("home_stats" in live_stats and
                         any(k in live_stats.get("home_stats", {}) for k in ["Hits", "Errors", "Runs"])),
                        f"home_stats: {live_stats.get('home_stats', {})}"
                    )
                    
                    runner.test(
                        "live_stats has away_stats with Hits/Errors/Runs",
                        ("away_stats" in live_stats and
                         any(k in live_stats.get("away_stats", {}) for k in ["Hits", "Errors", "Runs"])),
                        f"away_stats: {live_stats.get('away_stats', {})}"
                    )
                
                runner.test(
                    "Live item has _live_interpreter",
                    "_live_interpreter" in item,
                    f"Has interpreter: {'_live_interpreter' in item}"
                )
                
                if "_live_interpreter" in item:
                    interp = item["_live_interpreter"]
                    
                    runner.test(
                        "_live_interpreter has title",
                        "title" in interp,
                        f"title: {interp.get('title')}"
                    )
                    
                    runner.test(
                        "_live_interpreter has action",
                        "action" in interp,
                        f"action: {interp.get('action')}"
                    )
                    
                    runner.test(
                        "_live_interpreter has why",
                        "why" in interp,
                        f"why: {interp.get('why')}"
                    )
                    
                    runner.test(
                        "_live_interpreter has market_suggestion",
                        "market_suggestion" in interp,
                        f"market_suggestion: {interp.get('market_suggestion')}"
                    )
        
    except Exception as e:
        print(f"\n❌ ERROR testing live API: {e}")
        import traceback
        traceback.print_exc()
        return False
    
    return runner.summary()


def test_mlb_day_api():
    """Test /api/mlb/day endpoint for veto integration"""
    print("\n" + "="*70)
    print("TASK A — MLB DAY API VETO INTEGRATION")
    print("="*70)
    
    runner = TestRunner()
    
    try:
        url = f"{BACKEND_URL}/api/mlb/day"
        
        print(f"\n📡 GET {url}")
        response = requests.get(url, timeout=30)
        
        runner.test(
            "/api/mlb/day returns 200",
            response.status_code == 200,
            f"Status: {response.status_code}"
        )
        
        if response.status_code == 200:
            data = response.json()
            
            runner.test(
                "/api/mlb/day returns picks array",
                "picks" in data,
                f"Keys: {list(data.keys())}"
            )
            
            picks = data.get("picks", [])
            
            print(f"\n📊 Found {len(picks)} picks")
            
            # Look for any pick with under_veto data
            veto_found = False
            for pick in picks:
                if "under_veto" in pick or "under_veto_block" in pick:
                    veto_found = True
                    print(f"\n✅ Found pick with veto data:")
                    print(f"   Match: {pick.get('match_label', 'Unknown')}")
                    print(f"   Market: {pick.get('recommendation', {}).get('market', 'Unknown')}")
                    if "under_veto" in pick:
                        print(f"   under_veto: {pick['under_veto']}")
                    if "under_veto_block" in pick:
                        print(f"   under_veto_block: {pick['under_veto_block']}")
                    break
            
            runner.test(
                "/api/mlb/day exposes under_veto data on picks",
                veto_found or len(picks) == 0,  # Pass if no picks (off-season) or veto found
                f"Veto data found: {veto_found}, Total picks: {len(picks)}"
            )
            
    except Exception as e:
        print(f"\n❌ ERROR testing mlb/day API: {e}")
        import traceback
        traceback.print_exc()
        return False
    
    return runner.summary()


def main():
    """Run all backend tests"""
    print("\n" + "="*70)
    print("MLB UNDER VETO LAYER + LIVE BASEBALL BACKEND TEST SUITE")
    print("="*70)
    print(f"Backend URL: {BACKEND_URL}")
    print(f"Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    
    results = []
    
    # Task A: Veto Layer Functions
    results.append(("Veto Layer Functions", test_veto_layer_functions()))
    
    # Task A: MLB Day API
    results.append(("MLB Day API", test_mlb_day_api()))
    
    # Task B: MLB Live API
    results.append(("MLB Live API", test_mlb_live_api()))
    
    # Final summary
    print("\n" + "="*70)
    print("FINAL SUMMARY")
    print("="*70)
    
    all_passed = all(r[1] for r in results)
    
    for name, passed in results:
        status = "✅ PASSED" if passed else "❌ FAILED"
        print(f"{status} - {name}")
    
    print("\n" + "="*70)
    if all_passed:
        print("✅ ALL TEST SUITES PASSED")
        return 0
    else:
        print("❌ SOME TEST SUITES FAILED")
        return 1


if __name__ == "__main__":
    sys.exit(main())
