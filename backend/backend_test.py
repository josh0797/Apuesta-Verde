"""Backend testing for MLB Under Profile + 4 New Scrapers feature.

Tests:
1. Python imports for all new modules
2. Signal catalog - 6 new signals are baseball-only
3. mlb_starter_lineup_under_profile() with Phillies-Cleveland validated case
4. rescue_mlb_pitchers consults 6 sources (was 4)
5. Orchestrator includes under_profile in output
6. Regression: existing signals still work, API endpoints still work
"""
import sys
import asyncio
from datetime import datetime

# Test configuration
BACKEND_URL = "https://low-volatility-plays.preview.emergentagent.com"
DEMO_EMAIL = "demo@valuebet.app"
DEMO_PASSWORD = "demo1234"

class TestRunner:
    def __init__(self):
        self.tests_run = 0
        self.tests_passed = 0
        self.tests_failed = 0
        self.failures = []

    def test(self, name: str, condition: bool, details: str = ""):
        """Run a single test assertion."""
        self.tests_run += 1
        if condition:
            self.tests_passed += 1
            print(f"✅ PASS: {name}")
            if details:
                print(f"   {details}")
        else:
            self.tests_failed += 1
            self.failures.append(name)
            print(f"❌ FAIL: {name}")
            if details:
                print(f"   {details}")

    def summary(self):
        """Print test summary."""
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
    print("BACKEND TESTING: MLB Under Profile + 4 New Scrapers")
    print("="*70)
    
    # ═══════════════════════════════════════════════════════════════════════
    # TEST 1: Python Imports
    # ═══════════════════════════════════════════════════════════════════════
    print("\n[TEST 1] Python Imports")
    print("-"*70)
    
    try:
        from services.mlb_pregame_analytics import mlb_starter_lineup_under_profile
        runner.test(
            "Import mlb_starter_lineup_under_profile",
            True,
            "Function imported successfully"
        )
    except Exception as e:
        runner.test(
            "Import mlb_starter_lineup_under_profile",
            False,
            f"Import failed: {e}"
        )
    
    try:
        from services.signal_catalog import make_signal, SIGNAL_CATALOG
        runner.test(
            "Import signal_catalog",
            True,
            f"Catalog has {len(SIGNAL_CATALOG)} signals"
        )
    except Exception as e:
        runner.test(
            "Import signal_catalog",
            False,
            f"Import failed: {e}"
        )
    
    try:
        from services.external_sources import rotogrinders_mlb
        runner.test(
            "Import rotogrinders_mlb",
            hasattr(rotogrinders_mlb, 'fetch_lineups'),
            "Module has fetch_lineups function"
        )
    except Exception as e:
        runner.test(
            "Import rotogrinders_mlb",
            False,
            f"Import failed: {e}"
        )
    
    try:
        from services.external_sources import fantasyalarm_mlb
        runner.test(
            "Import fantasyalarm_mlb",
            hasattr(fantasyalarm_mlb, 'fetch_lineups'),
            "Module has fetch_lineups function"
        )
    except Exception as e:
        runner.test(
            "Import fantasyalarm_mlb",
            False,
            f"Import failed: {e}"
        )
    
    try:
        from services.external_sources import sofascore_basketball
        runner.test(
            "Import sofascore_basketball",
            hasattr(sofascore_basketball, 'fetch_matchups'),
            "Module has fetch_matchups function"
        )
    except Exception as e:
        runner.test(
            "Import sofascore_basketball",
            False,
            f"Import failed: {e}"
        )
    
    try:
        from services.external_sources import flashscore_basketball
        runner.test(
            "Import flashscore_basketball",
            hasattr(flashscore_basketball, 'fetch_matchups'),
            "Module has fetch_matchups function"
        )
    except Exception as e:
        runner.test(
            "Import flashscore_basketball",
            False,
            f"Import failed: {e}"
        )
    
    # ═══════════════════════════════════════════════════════════════════════
    # TEST 2: Signal Catalog - 6 New Signals are Baseball-Only
    # ═══════════════════════════════════════════════════════════════════════
    print("\n[TEST 2] Signal Catalog - Baseball-Only Signals")
    print("-"*70)
    
    try:
        from services.signal_catalog import make_signal, SIGNAL_CATALOG, BASEBALL_ONLY
        
        new_signals = [
            "STRONG_STARTING_PITCHER_PROFILE",
            "UNDER_TREND_DETECTED",
            "EARLY_INNING_UNDER_DEPENDENCY",
            "LOW_SCORING_GAME_SCRIPT",
            "PROTECTED_TOTAL_MARKET",
            "H2H_LOW_TOTAL_PATTERN"
        ]
        
        for signal_code in new_signals:
            # Check signal exists in catalog
            exists = signal_code in SIGNAL_CATALOG
            runner.test(
                f"Signal {signal_code} exists in catalog",
                exists,
                f"Found: {exists}"
            )
            
            if exists:
                # Check it's baseball-only
                entry = SIGNAL_CATALOG[signal_code]
                is_baseball_only = entry.get("applicable_sports") == BASEBALL_ONLY
                runner.test(
                    f"Signal {signal_code} is baseball-only",
                    is_baseball_only,
                    f"applicable_sports: {entry.get('applicable_sports')}"
                )
                
                # Test make_signal with sport='baseball' returns signal
                sig_baseball = make_signal(signal_code, sport="baseball")
                runner.test(
                    f"make_signal('{signal_code}', sport='baseball') returns signal",
                    sig_baseball is not None,
                    f"Returned: {type(sig_baseball)}"
                )
                
                # Test make_signal with sport='football' returns None
                sig_football = make_signal(signal_code, sport="football")
                runner.test(
                    f"make_signal('{signal_code}', sport='football') returns None",
                    sig_football is None,
                    f"Correctly filtered cross-sport signal"
                )
    
    except Exception as e:
        runner.test(
            "Signal catalog tests",
            False,
            f"Error: {e}"
        )
    
    # ═══════════════════════════════════════════════════════════════════════
    # TEST 3: mlb_starter_lineup_under_profile() - Phillies-Cleveland Case
    # ═══════════════════════════════════════════════════════════════════════
    print("\n[TEST 3] mlb_starter_lineup_under_profile() - Validated Case")
    print("-"*70)
    
    try:
        from services.mlb_pregame_analytics import mlb_starter_lineup_under_profile
        
        # Phillies-Cleveland validated context from the spec
        ctx = {
            "home_pitcher_quality": {"score": 90, "era": 1.85, "whip": 1.05},
            "away_pitcher_quality": {"score": 75, "era": 3.10, "whip": 1.15},
            "home_pitcher_stats": {"era": 1.85, "whip": 1.05},
            "away_pitcher_stats": {"era": 3.10, "whip": 1.15},
            "offense_home": {"score": 45},
            "offense_away": {"score": 42},
            "park": {"park_runs_mult": 1.02, "weather_score": 50},
            "bullpen": {"score": 65, "tags": []},
            "h2h_recent": [
                {"home_score": 2, "away_score": 3},
                {"home_score": 1, "away_score": 4},
                {"home_score": 3, "away_score": 2},
                {"home_score": 2, "away_score": 1},
                {"home_score": 1, "away_score": 0}
            ],
            "home_il_count": 1,
            "away_il_count": 1,
            "fragility": {"score": 25}
        }
        
        result = mlb_starter_lineup_under_profile(ctx, book_line=7.5)
        
        # Test classification
        runner.test(
            "Classification is VALUE_BET",
            result.get("classification") == "VALUE_BET",
            f"Got: {result.get('classification')}"
        )
        
        # Test selection
        runner.test(
            "Selection is 'Under 7.5'",
            result.get("selection") == "Under 7.5",
            f"Got: {result.get('selection')}"
        )
        
        # Test underProfileScore >= 75
        score = result.get("underProfileScore", 0)
        runner.test(
            "underProfileScore >= 75",
            score >= 75,
            f"Score: {score}"
        )
        
        # Test required signals are present
        signals = result.get("signals", [])
        expected_signals = [
            "STRONG_STARTING_PITCHER_PROFILE",
            "H2H_LOW_TOTAL_PATTERN",
            "UNDER_TREND_DETECTED",
            "PROTECTED_TOTAL_MARKET"
        ]
        
        for expected in expected_signals:
            found = expected in signals
            runner.test(
                f"Signal '{expected}' present",
                found,
                f"Signals: {signals}"
            )
        
        # Test structure completeness
        required_fields = [
            "classification", "confidence", "underProfileScore", "fragilityScore",
            "reasons", "risks", "signals", "alt_markets", "early_inning_dependency"
        ]
        
        for field in required_fields:
            runner.test(
                f"Field '{field}' present in result",
                field in result,
                f"Type: {type(result.get(field))}"
            )
    
    except Exception as e:
        runner.test(
            "mlb_starter_lineup_under_profile() test",
            False,
            f"Error: {e}"
        )
        import traceback
        traceback.print_exc()
    
    # ═══════════════════════════════════════════════════════════════════════
    # TEST 4: rescue_mlb_pitchers Consults 6 Sources
    # ═══════════════════════════════════════════════════════════════════════
    print("\n[TEST 4] rescue_mlb_pitchers - 6 Sources Consulted")
    print("-"*70)
    
    try:
        from services.external_sources.mlb_lineup_rescue import rescue_mlb_pitchers, ALL_SCRAPERS
        
        # Check ALL_SCRAPERS has 6 entries
        runner.test(
            "ALL_SCRAPERS has 6 entries",
            len(ALL_SCRAPERS) == 6,
            f"Count: {len(ALL_SCRAPERS)}"
        )
        
        # Check rotogrinders_mlb and fantasyalarm_mlb are in the list
        scraper_names = [s[0] for s in ALL_SCRAPERS]
        runner.test(
            "rotogrinders_mlb_lineups in ALL_SCRAPERS",
            "rotogrinders_mlb_lineups" in scraper_names,
            f"Scrapers: {scraper_names}"
        )
        
        runner.test(
            "fantasyalarm_mlb_lineups in ALL_SCRAPERS",
            "fantasyalarm_mlb_lineups" in scraper_names,
            f"Scrapers: {scraper_names}"
        )
        
        # Test rescue_mlb_pitchers with a fake game
        fake_games = [{
            "gamePk": 12345,
            "home_team": "Philadelphia Phillies",
            "away_team": "Cleveland Guardians",
            "home_probable_id": None,
            "away_probable_id": None
        }]
        
        print("   Calling rescue_mlb_pitchers (this may take a few seconds)...")
        results = await rescue_mlb_pitchers("2024-05-22", fake_games)
        
        runner.test(
            "rescue_mlb_pitchers returns results",
            len(results) == 1,
            f"Returned {len(results)} results"
        )
        
        if results:
            result = results[0]
            sources_consulted = result.get("sources_consulted", [])
            
            runner.test(
                "sources_consulted has 6 entries",
                len(sources_consulted) == 6,
                f"Count: {len(sources_consulted)}"
            )
            
            # Check that rotogrinders and fantasyalarm were called
            source_names = [s.get("source") for s in sources_consulted]
            runner.test(
                "rotogrinders_mlb_lineups was consulted",
                "rotogrinders_mlb_lineups" in source_names,
                f"Sources: {source_names}"
            )
            
            runner.test(
                "fantasyalarm_mlb_lineups was consulted",
                "fantasyalarm_mlb_lineups" in source_names,
                f"Sources: {source_names}"
            )
            
            # Note: It's OK if sources have status='failed' - the contract is
            # that they were CALLED, not that they succeeded
            print(f"   Sources consulted: {source_names}")
            for s in sources_consulted:
                print(f"     - {s.get('source')}: {s.get('status')}")
    
    except Exception as e:
        runner.test(
            "rescue_mlb_pitchers test",
            False,
            f"Error: {e}"
        )
        import traceback
        traceback.print_exc()
    
    # ═══════════════════════════════════════════════════════════════════════
    # TEST 5: Orchestrator Integration (using mock DB)
    # ═══════════════════════════════════════════════════════════════════════
    print("\n[TEST 5] Orchestrator Integration - under_profile Field")
    print("-"*70)
    
    try:
        from services.mlb_day_orchestrator import analyze_mlb_day
        
        # Create a minimal fake DB object
        class FakeDB:
            pass
        
        fake_db = FakeDB()
        
        print("   Calling analyze_mlb_day (this may take a few seconds)...")
        # Use empty date_str to get "today" in Eastern time
        result = await analyze_mlb_day("", db=fake_db)
        
        runner.test(
            "analyze_mlb_day returns dict",
            isinstance(result, dict),
            f"Type: {type(result)}"
        )
        
        # Check for expected top-level keys
        expected_keys = ["picks", "rescued_picks", "discarded_picks", "pipeline_meta"]
        for key in expected_keys:
            runner.test(
                f"Result has '{key}' field",
                key in result,
                f"Keys: {list(result.keys())}"
            )
        
        # Check if any picks have under_profile field
        picks = result.get("picks", [])
        rescued = result.get("rescued_picks", [])
        all_picks = picks + rescued
        
        if all_picks:
            # Check first pick for under_profile
            first_pick = all_picks[0]
            runner.test(
                "Pick has 'under_profile' field",
                "under_profile" in first_pick,
                f"Pick keys: {list(first_pick.keys())}"
            )
            
            if "under_profile" in first_pick:
                up = first_pick["under_profile"]
                runner.test(
                    "under_profile has 'classification' field",
                    "classification" in up,
                    f"Classification: {up.get('classification')}"
                )
                
                runner.test(
                    "under_profile has 'underProfileScore' field",
                    "underProfileScore" in up,
                    f"Score: {up.get('underProfileScore')}"
                )
            
            # Check for editorial_context_signals
            if "editorial_context_signals" in first_pick:
                signals = first_pick["editorial_context_signals"]
                signal_codes = [s.get("code") for s in signals if isinstance(s, dict)]
                print(f"   Editorial signals found: {signal_codes}")
        else:
            print("   No picks returned (this is OK - may be no games today)")
            runner.test(
                "Pipeline meta explains why no picks",
                "abort_reason" in result.get("pipeline_meta", {}),
                f"Abort reason: {result.get('pipeline_meta', {}).get('abort_reason')}"
            )
    
    except Exception as e:
        runner.test(
            "Orchestrator integration test",
            False,
            f"Error: {e}"
        )
        import traceback
        traceback.print_exc()
    
    # ═══════════════════════════════════════════════════════════════════════
    # TEST 6: Regression - Existing Signals Still Work
    # ═══════════════════════════════════════════════════════════════════════
    print("\n[TEST 6] Regression - Existing Signals")
    print("-"*70)
    
    try:
        from services.signal_catalog import make_signal
        
        existing_signals = [
            "PITCHER_NOT_CONFIRMED",
            "MLB_COM_FALLBACK_USED",
            "IL_DEPTH_RISK",
            "EXTERNAL_SOURCE_USED",
            "PITCHER_CONFIRMED_EXTERNAL"
        ]
        
        for signal_code in existing_signals:
            sig = make_signal(signal_code, sport="baseball")
            runner.test(
                f"Existing signal '{signal_code}' still works",
                sig is not None,
                f"Label: {sig.get('label') if sig else 'None'}"
            )
    
    except Exception as e:
        runner.test(
            "Regression test - existing signals",
            False,
            f"Error: {e}"
        )
    
    # ═══════════════════════════════════════════════════════════════════════
    # TEST 7: API Endpoint Regression (if we can reach the server)
    # ═══════════════════════════════════════════════════════════════════════
    print("\n[TEST 7] API Endpoint Regression")
    print("-"*70)
    
    try:
        import httpx
        
        # Test health endpoint
        async with httpx.AsyncClient(timeout=10.0) as client:
            try:
                resp = await client.get(f"{BACKEND_URL}/api/")
                runner.test(
                    "GET /api/ returns 200",
                    resp.status_code == 200,
                    f"Status: {resp.status_code}"
                )
            except Exception as e:
                runner.test(
                    "GET /api/ returns 200",
                    False,
                    f"Request failed: {e}"
                )
            
            # Test login
            try:
                resp = await client.post(
                    f"{BACKEND_URL}/api/auth/login",
                    json={"email": DEMO_EMAIL, "password": DEMO_PASSWORD}
                )
                runner.test(
                    "POST /api/auth/login returns 200",
                    resp.status_code == 200,
                    f"Status: {resp.status_code}"
                )
                
                if resp.status_code == 200:
                    data = resp.json()
                    token = data.get("access_token")
                    
                    if token:
                        # Test analysis/run endpoint for baseball
                        headers = {"Authorization": f"Bearer {token}"}
                        try:
                            resp = await client.post(
                                f"{BACKEND_URL}/api/analysis/run",
                                json={
                                    "sport": "baseball",
                                    "refresh": False,
                                    "include_live": False,
                                    "max_matches": 3,
                                    "background": False
                                },
                                headers=headers,
                                timeout=30.0
                            )
                            runner.test(
                                "POST /api/analysis/run (baseball) returns 200",
                                resp.status_code == 200,
                                f"Status: {resp.status_code}"
                            )
                            
                            if resp.status_code == 200:
                                data = resp.json()
                                runner.test(
                                    "Response has 'pipeline_meta' field",
                                    "pipeline_meta" in data,
                                    f"Keys: {list(data.keys())}"
                                )
                        except Exception as e:
                            runner.test(
                                "POST /api/analysis/run (baseball) returns 200",
                                False,
                                f"Request failed: {e}"
                            )
                        
                        # Test analysis/run endpoint for basketball
                        try:
                            resp = await client.post(
                                f"{BACKEND_URL}/api/analysis/run",
                                json={
                                    "sport": "basketball",
                                    "refresh": False,
                                    "include_live": False,
                                    "max_matches": 3,
                                    "background": False
                                },
                                headers=headers,
                                timeout=30.0
                            )
                            runner.test(
                                "POST /api/analysis/run (basketball) returns 200",
                                resp.status_code == 200,
                                f"Status: {resp.status_code}"
                            )
                            
                            if resp.status_code == 200:
                                data = resp.json()
                                # Check for ESPN fallback field
                                runner.test(
                                    "Basketball response structure valid",
                                    "pipeline_meta" in data or "picks" in data,
                                    f"Keys: {list(data.keys())}"
                                )
                        except Exception as e:
                            runner.test(
                                "POST /api/analysis/run (basketball) returns 200",
                                False,
                                f"Request failed: {e}"
                            )
            
            except Exception as e:
                runner.test(
                    "POST /api/auth/login returns 200",
                    False,
                    f"Request failed: {e}"
                )
    
    except Exception as e:
        runner.test(
            "API endpoint regression tests",
            False,
            f"Error: {e}"
        )
    
    # Print summary
    success = runner.summary()
    return 0 if success else 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
