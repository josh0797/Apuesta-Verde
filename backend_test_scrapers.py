"""Backend Testing for Multi-Sport External Scrapers Integration
Tests the wiring of 4 new scrapers (rotogrinders_mlb, fantasyalarm_mlb, sofascore_basketball, flashscore_basketball)
into the rescue/ingestion layers.

Test Coverage:
1. Import sanity for all new modules
2. POST /api/analysis/run sport=basketball - new pipeline_meta keys, fail-soft behavior
3. POST /api/analysis/run sport=baseball - 6 scrapers consulted, external_rescue_count
4. GET /api/mlb/day?date=YYYY-MM-DD - regression
5. Auth POST /api/auth/login - demo credentials
6. Football pipeline - no regression, no basketball/MLB keys
7. basketball_rescue.attach_evidence - synthetic data test
8. Existing endpoints - regression tests
"""
import sys
import asyncio
import json
from datetime import datetime, timedelta

try:
    import requests
except ImportError:
    print("Installing requests...")
    import subprocess
    subprocess.check_call([sys.executable, "-m", "pip", "install", "requests"])
    import requests

# Configuration
BACKEND_URL = "https://low-volatility-plays.preview.emergentagent.com"
DEMO_EMAIL = "demo@valuebet.app"
DEMO_PASSWORD = "demo1234"

class TestRunner:
    def __init__(self):
        self.tests_run = 0
        self.tests_passed = 0
        self.tests_failed = 0
        self.failures = []
        self.token = None

    def log(self, message, level="INFO"):
        timestamp = datetime.now().strftime("%H:%M:%S")
        print(f"[{timestamp}] {level}: {message}")

    def test(self, name: str, condition: bool, details: str = ""):
        self.tests_run += 1
        if condition:
            self.tests_passed += 1
            self.log(f"✅ PASS: {name}", "PASS")
            if details:
                print(f"   {details}")
        else:
            self.tests_failed += 1
            self.failures.append(name)
            self.log(f"❌ FAIL: {name}", "FAIL")
            if details:
                print(f"   {details}")

    def summary(self):
        print("\n" + "="*70)
        print(f"TEST SUMMARY: {self.tests_passed}/{self.tests_run} passed")
        if self.failures:
            print(f"\nFailed tests:")
            for f in self.failures:
                print(f"  - {f}")
        print("="*70)
        return self.tests_failed == 0


async def main():
    runner = TestRunner()
    
    print("="*70)
    print("BACKEND TESTING: Multi-Sport External Scrapers Integration")
    print("="*70)
    
    # ═══════════════════════════════════════════════════════════════════════
    # TEST 1: Python Import Sanity Checks
    # ═══════════════════════════════════════════════════════════════════════
    print("\n[TEST 1] Python Import Sanity Checks")
    print("-"*70)
    
    import_tests = [
        ("services.external_sources.basketball_rescue", ["rescue_basketball_day", "attach_evidence"]),
        ("services.external_sources.sofascore_basketball", ["fetch_matchups"]),
        ("services.external_sources.flashscore_basketball", ["fetch_matchups"]),
        ("services.external_sources.rotogrinders_mlb", ["fetch_lineups"]),
        ("services.external_sources.fantasyalarm_mlb", ["fetch_lineups"]),
        ("services.external_sources.mlb_lineup_rescue", ["rescue_mlb_pitchers", "ALL_SCRAPERS"]),
        ("services.data_ingestion", ["ingest_basketball_sofascore_fallback", "normalize_sofascore_basketball_game"]),
    ]
    
    sys.path.insert(0, '/app/backend')
    
    for module_name, expected_attrs in import_tests:
        try:
            module = __import__(module_name, fromlist=expected_attrs)
            for attr in expected_attrs:
                has_attr = hasattr(module, attr)
                runner.test(
                    f"Import {module_name}.{attr}",
                    has_attr,
                    f"Module has {attr}: {has_attr}"
                )
        except Exception as e:
            for attr in expected_attrs:
                runner.test(
                    f"Import {module_name}.{attr}",
                    False,
                    f"Import failed: {e}"
                )
    
    # ═══════════════════════════════════════════════════════════════════════
    # TEST 2: Verify MLB Rescue Has 6 Scrapers
    # ═══════════════════════════════════════════════════════════════════════
    print("\n[TEST 2] MLB Rescue - 6 Scrapers Configured")
    print("-"*70)
    
    try:
        from services.external_sources.mlb_lineup_rescue import ALL_SCRAPERS
        
        runner.test(
            "ALL_SCRAPERS has 6 entries",
            len(ALL_SCRAPERS) == 6,
            f"Count: {len(ALL_SCRAPERS)}"
        )
        
        scraper_names = [s[0] for s in ALL_SCRAPERS]
        expected_scrapers = [
            "rotowire_mlb_lineups",
            "mlb_official_lineups",
            "fantasypros_mlb_lineups",
            "espn_mlb_scoreboard",
            "rotogrinders_mlb_lineups",
            "fantasyalarm_mlb_lineups"
        ]
        
        for expected in expected_scrapers:
            runner.test(
                f"Scraper '{expected}' in ALL_SCRAPERS",
                expected in scraper_names,
                f"Found: {expected in scraper_names}"
            )
        
        print(f"   Configured scrapers: {scraper_names}")
        
    except Exception as e:
        runner.test("MLB Rescue configuration", False, f"Error: {e}")
    
    # ═══════════════════════════════════════════════════════════════════════
    # TEST 3: Basketball Rescue - attach_evidence with Synthetic Data
    # ═══════════════════════════════════════════════════════════════════════
    print("\n[TEST 3] Basketball Rescue - attach_evidence Synthetic Test")
    print("-"*70)
    
    try:
        from services.external_sources.basketball_rescue import attach_evidence
        
        # Synthetic matches
        matches = [
            {
                "match_id": "test_bk_1",
                "sport": "basketball",
                "home_team": {"name": "Los Angeles Lakers"},
                "away_team": {"name": "Boston Celtics"},
            },
            {
                "match_id": "test_bk_2",
                "sport": "basketball",
                "home_team": {"name": "Golden State Warriors"},
                "away_team": {"name": "Miami Heat"},
            },
            {
                "match_id": "test_fb_1",
                "sport": "football",  # Should be skipped
                "home_team": {"name": "Real Madrid"},
                "away_team": {"name": "Barcelona"},
            }
        ]
        
        # Synthetic rescue payload
        rescue_payload = {
            "matchups": {
                "boston celtics@los angeles lakers": {
                    "home_team": "Los Angeles Lakers",
                    "away_team": "Boston Celtics",
                    "league": "NBA",
                    "kickoff_ts": 1234567890,
                    "_primary_source": "sofascore_basketball",
                    "_corroborated_by": ["flashscore_basketball"]
                }
            },
            "sources_consulted": [
                {"source": "sofascore_basketball", "status": "success"},
                {"source": "flashscore_basketball", "status": "success"}
            ]
        }
        
        attached_count = attach_evidence(matches, rescue_payload)
        
        runner.test(
            "attach_evidence returns count",
            attached_count == 1,
            f"Attached evidence to {attached_count} matches (expected 1)"
        )
        
        # Check Lakers-Celtics match has evidence
        lakers_match = matches[0]
        runner.test(
            "Lakers-Celtics has _external_evidence",
            "_external_evidence" in lakers_match,
            f"Evidence attached: {'_external_evidence' in lakers_match}"
        )
        
        if "_external_evidence" in lakers_match:
            evidence = lakers_match["_external_evidence"]
            runner.test(
                "Evidence found=True",
                evidence.get("found") == True,
                f"found: {evidence.get('found')}"
            )
            runner.test(
                "Evidence has primary_source",
                evidence.get("primary_source") == "sofascore_basketball",
                f"primary_source: {evidence.get('primary_source')}"
            )
            runner.test(
                "Evidence has corroborated_by",
                "flashscore_basketball" in evidence.get("corroborated_by", []),
                f"corroborated_by: {evidence.get('corroborated_by')}"
            )
        
        # Check Warriors-Heat has no evidence (not in rescue payload)
        warriors_match = matches[1]
        if "_external_evidence" in warriors_match:
            runner.test(
                "Warriors-Heat evidence found=False",
                warriors_match["_external_evidence"].get("found") == False,
                f"found: {warriors_match['_external_evidence'].get('found')}"
            )
        
        # Check football match was skipped
        football_match = matches[2]
        runner.test(
            "Football match skipped (no evidence)",
            "_external_evidence" not in football_match or football_match["_external_evidence"].get("found") == False,
            f"Football match correctly skipped"
        )
        
    except Exception as e:
        runner.test("Basketball attach_evidence test", False, f"Error: {e}")
        import traceback
        traceback.print_exc()
    
    # ═══════════════════════════════════════════════════════════════════════
    # TEST 4: Auth - Login
    # ═══════════════════════════════════════════════════════════════════════
    print("\n[TEST 4] Authentication")
    print("-"*70)
    
    try:
        response = requests.post(
            f"{BACKEND_URL}/api/auth/login",
            json={"email": DEMO_EMAIL, "password": DEMO_PASSWORD},
            timeout=10
        )
        
        runner.test(
            "POST /api/auth/login returns 200",
            response.status_code == 200,
            f"Status: {response.status_code}"
        )
        
        if response.status_code == 200:
            data = response.json()
            runner.test(
                "Response has token",
                "token" in data,
                f"Token present: {'token' in data}"
            )
            if "token" in data:
                runner.token = data["token"]
                print(f"   Token acquired: {runner.token[:20]}...")
        
    except Exception as e:
        runner.test("Auth login", False, f"Error: {e}")
    
    if not runner.token:
        print("\n❌ Cannot proceed without authentication token")
        return runner.summary()
    
    headers = {"Authorization": f"Bearer {runner.token}"}
    
    # ═══════════════════════════════════════════════════════════════════════
    # TEST 5: Basketball Pipeline - New Keys & Fail-Soft
    # ═══════════════════════════════════════════════════════════════════════
    print("\n[TEST 5] Basketball Pipeline - POST /api/analysis/run")
    print("-"*70)
    print("   Note: External scrapers will fail (no BRIGHTDATA_API_KEY) - this is EXPECTED")
    
    try:
        response = requests.post(
            f"{BACKEND_URL}/api/analysis/run",
            json={
                "sport": "basketball",
                "refresh": False,
                "include_live": False,
                "max_matches": 3,
                "background": False
            },
            headers=headers,
            timeout=60
        )
        
        runner.test(
            "POST /api/analysis/run (basketball) returns 200",
            response.status_code == 200,
            f"Status: {response.status_code}"
        )
        
        if response.status_code == 200:
            data = response.json()
            
            runner.test(
                "Response has pipeline_meta",
                "pipeline_meta" in data,
                f"Keys: {list(data.keys())}"
            )
            
            if "pipeline_meta" in data:
                meta = data["pipeline_meta"]
                
                # Check for new basketball keys
                expected_keys = [
                    "sofascore_basketball_games_found",
                    "basketball_external_corroborated",
                    "external_sources_consulted"
                ]
                
                for key in expected_keys:
                    runner.test(
                        f"pipeline_meta has '{key}'",
                        key in meta,
                        f"Value: {meta.get(key)}"
                    )
                
                # Check external_sources_consulted structure
                if "external_sources_consulted" in meta:
                    sources = meta["external_sources_consulted"]
                    runner.test(
                        "external_sources_consulted is list",
                        isinstance(sources, list),
                        f"Type: {type(sources)}"
                    )
                    
                    if sources:
                        print(f"   External sources consulted: {len(sources)}")
                        for src in sources:
                            status = src.get("status", "unknown")
                            reason = src.get("reason", "")
                            print(f"     - {src.get('source')}: {status} {f'({reason})' if reason else ''}")
                        
                        # Verify fail-soft: status should be 'failed' with reason
                        failed_sources = [s for s in sources if s.get("status") == "failed"]
                        if failed_sources:
                            print(f"   ✓ Fail-soft working: {len(failed_sources)} sources failed gracefully")
                
                # Verify basketball_external_corroborated is a number
                if "basketball_external_corroborated" in meta:
                    corr = meta["basketball_external_corroborated"]
                    runner.test(
                        "basketball_external_corroborated is int",
                        isinstance(corr, int),
                        f"Value: {corr}"
                    )
        
    except requests.Timeout:
        runner.test("Basketball pipeline timeout", False, "Request timed out after 60s")
    except Exception as e:
        runner.test("Basketball pipeline", False, f"Error: {e}")
    
    # ═══════════════════════════════════════════════════════════════════════
    # TEST 6: Baseball Pipeline - 6 Scrapers Consulted
    # ═══════════════════════════════════════════════════════════════════════
    print("\n[TEST 6] Baseball Pipeline - POST /api/analysis/run")
    print("-"*70)
    print("   Note: This can take 60-120s due to LLM analysis. Using 180s timeout.")
    
    try:
        response = requests.post(
            f"{BACKEND_URL}/api/analysis/run",
            json={
                "sport": "baseball",
                "refresh": False,
                "include_live": False,
                "max_matches": 2,
                "background": False
            },
            headers=headers,
            timeout=180
        )
        
        runner.test(
            "POST /api/analysis/run (baseball) returns 200",
            response.status_code == 200,
            f"Status: {response.status_code}"
        )
        
        if response.status_code == 200:
            data = response.json()
            
            runner.test(
                "Response has pipeline_meta",
                "pipeline_meta" in data,
                f"Keys: {list(data.keys())}"
            )
            
            if "pipeline_meta" in data:
                meta = data["pipeline_meta"]
                
                # Check for MLB rescue keys
                expected_keys = [
                    "external_rescue_count",
                    "external_sources_consulted"
                ]
                
                for key in expected_keys:
                    runner.test(
                        f"pipeline_meta has '{key}'",
                        key in meta,
                        f"Value: {meta.get(key)}"
                    )
                
                # Check external_sources_consulted for 6 MLB scrapers
                if "external_sources_consulted" in meta:
                    sources = meta["external_sources_consulted"]
                    
                    if sources:
                        print(f"   External sources consulted: {len(sources)}")
                        
                        mlb_scrapers = [
                            "rotowire_mlb_lineups",
                            "mlb_official_lineups",
                            "fantasypros_mlb_lineups",
                            "espn_mlb_scoreboard",
                            "rotogrinders_mlb_lineups",
                            "fantasyalarm_mlb_lineups"
                        ]
                        
                        source_names = [s.get("source") for s in sources]
                        
                        for scraper in mlb_scrapers:
                            runner.test(
                                f"MLB scraper '{scraper}' consulted",
                                scraper in source_names,
                                f"Found: {scraper in source_names}"
                            )
                        
                        # Print status of each scraper
                        for src in sources:
                            if src.get("source") in mlb_scrapers:
                                status = src.get("status", "unknown")
                                reason = src.get("reason", "")
                                print(f"     - {src.get('source')}: {status} {f'({reason})' if reason else ''}")
                
                # Check external_rescue_count
                if "external_rescue_count" in meta:
                    rescue_count = meta["external_rescue_count"]
                    runner.test(
                        "external_rescue_count is int",
                        isinstance(rescue_count, int),
                        f"Rescued: {rescue_count} games"
                    )
        
    except requests.Timeout:
        runner.test("Baseball pipeline timeout", False, "Request timed out after 180s")
    except Exception as e:
        runner.test("Baseball pipeline", False, f"Error: {e}")
    
    # ═══════════════════════════════════════════════════════════════════════
    # TEST 7: Football Pipeline - No Regression
    # ═══════════════════════════════════════════════════════════════════════
    print("\n[TEST 7] Football Pipeline - Regression Test")
    print("-"*70)
    
    try:
        response = requests.post(
            f"{BACKEND_URL}/api/analysis/run",
            json={
                "sport": "football",
                "refresh": False,
                "include_live": False,
                "max_matches": 3,
                "background": False
            },
            headers=headers,
            timeout=60
        )
        
        runner.test(
            "POST /api/analysis/run (football) returns 200",
            response.status_code == 200,
            f"Status: {response.status_code}"
        )
        
        if response.status_code == 200:
            data = response.json()
            
            runner.test(
                "Response has pipeline_meta",
                "pipeline_meta" in data,
                f"Keys: {list(data.keys())}"
            )
            
            if "pipeline_meta" in data:
                meta = data["pipeline_meta"]
                
                # Verify NO basketball/MLB keys in football pipeline
                basketball_keys = [
                    "sofascore_basketball_games_found",
                    "basketball_external_corroborated",
                    "espn_nba_games_found"
                ]
                
                mlb_keys = [
                    "mlb_stats_api_games_found",
                    "external_rescue_count"
                ]
                
                for key in basketball_keys:
                    runner.test(
                        f"Football pipeline does NOT have '{key}'",
                        key not in meta,
                        f"Correctly excluded: {key not in meta}"
                    )
                
                for key in mlb_keys:
                    runner.test(
                        f"Football pipeline does NOT have '{key}'",
                        key not in meta,
                        f"Correctly excluded: {key not in meta}"
                    )
        
    except Exception as e:
        runner.test("Football pipeline regression", False, f"Error: {e}")
    
    # ═══════════════════════════════════════════════════════════════════════
    # TEST 8: MLB Day Endpoint - Regression
    # ═══════════════════════════════════════════════════════════════════════
    print("\n[TEST 8] MLB Day Endpoint - GET /api/mlb/day")
    print("-"*70)
    
    try:
        # Use today's date
        today = datetime.now().strftime("%Y-%m-%d")
        
        response = requests.get(
            f"{BACKEND_URL}/api/mlb/day?date={today}",
            headers=headers,
            timeout=30
        )
        
        runner.test(
            f"GET /api/mlb/day?date={today} returns 200",
            response.status_code == 200,
            f"Status: {response.status_code}"
        )
        
        if response.status_code == 200:
            data = response.json()
            runner.test(
                "Response is dict",
                isinstance(data, dict),
                f"Type: {type(data)}"
            )
            
            # Check for expected structure
            if isinstance(data, dict):
                print(f"   Response keys: {list(data.keys())}")
        
    except Exception as e:
        runner.test("MLB day endpoint", False, f"Error: {e}")
    
    # ═══════════════════════════════════════════════════════════════════════
    # TEST 9: Existing Endpoints - Regression
    # ═══════════════════════════════════════════════════════════════════════
    print("\n[TEST 9] Existing Endpoints - Regression Tests")
    print("-"*70)
    
    endpoints = [
        ("GET /api/", "GET", "api/", None),
        ("GET /api/picks/today", "GET", "api/picks/today?sport=football", None),
        ("GET /api/matches/upcoming", "GET", "api/matches/upcoming?sport=football", None),
        ("GET /api/matches/live", "GET", "api/matches/live?sport=football", None),
    ]
    
    for name, method, endpoint, payload in endpoints:
        try:
            if method == "GET":
                response = requests.get(f"{BACKEND_URL}/{endpoint}", headers=headers, timeout=10)
            else:
                response = requests.post(f"{BACKEND_URL}/{endpoint}", json=payload, headers=headers, timeout=10)
            
            runner.test(
                f"{name} returns 200",
                response.status_code == 200,
                f"Status: {response.status_code}"
            )
        except Exception as e:
            runner.test(f"{name}", False, f"Error: {e}")
    
    # Print summary
    success = runner.summary()
    
    # Generate test report
    report = {
        "timestamp": datetime.now().isoformat(),
        "summary": {
            "total_tests": runner.tests_run,
            "passed": runner.tests_passed,
            "failed": runner.tests_failed,
            "success_rate": round((runner.tests_passed / runner.tests_run * 100) if runner.tests_run > 0 else 0, 2)
        },
        "failures": runner.failures,
        "test_categories": {
            "import_sanity": "Completed",
            "mlb_rescue_config": "Completed",
            "basketball_attach_evidence": "Completed",
            "auth": "Completed",
            "basketball_pipeline": "Completed",
            "baseball_pipeline": "Completed",
            "football_regression": "Completed",
            "mlb_day_endpoint": "Completed",
            "existing_endpoints": "Completed"
        }
    }
    
    return 0 if success else 1, report


if __name__ == "__main__":
    exit_code, report = asyncio.run(main())
    
    # Save report
    report_file = "/app/test_reports/iteration_38.json"
    with open(report_file, 'w') as f:
        json.dump(report, f, indent=2)
    
    print(f"\n📊 Test report saved to: {report_file}")
    sys.exit(exit_code)
