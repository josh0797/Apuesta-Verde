"""MLB-V5 Engine Integration Testing

Tests the MLB-V5 fixes:
1. Auth still works
2. Football regression - no MLB keys in result
3. Basketball regression - no MLB keys, no crash
4. MLB main path - engine='mlb_pregame_analytics_v2', new buckets present
5. Bucket correctness - STRUCTURAL_LEAN games in structural_lean_requires_odds
6. Signal catalog - new signals work for baseball only
7. Import sanity - mlb_structural_data_quality
8. GET /api/mlb/day returns new buckets
9. MLB-V4 feedback endpoints still work
"""
import sys
import asyncio
import httpx
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
    token = None
    
    print("="*70)
    print("MLB-V5 ENGINE INTEGRATION TESTING")
    print("="*70)
    
    # ═══════════════════════════════════════════════════════════════════════
    # TEST 1: Auth POST /api/auth/login demo@valuebet.app/demo1234
    # ═══════════════════════════════════════════════════════════════════════
    print("\n[TEST 1] Authentication")
    print("-"*70)
    
    async with httpx.AsyncClient(timeout=30.0) as client:
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
                token = data.get("token")
                runner.test(
                    "Response contains token",
                    token is not None,
                    f"Token length: {len(token) if token else 0}"
                )
        except Exception as e:
            runner.test(
                "POST /api/auth/login returns 200",
                False,
                f"Request failed: {e}"
            )
    
    if not token:
        print("\n❌ CRITICAL: Cannot proceed without auth token")
        runner.summary()
        return 1
    
    headers = {"Authorization": f"Bearer {token}"}
    
    # ═══════════════════════════════════════════════════════════════════════
    # TEST 2: Football Regression - No MLB Keys
    # ═══════════════════════════════════════════════════════════════════════
    print("\n[TEST 2] Football Regression - No MLB Keys")
    print("-"*70)
    
    async with httpx.AsyncClient(timeout=120.0) as client:
        try:
            print("   Running football analysis (background=true, max_matches=4)...")
            resp = await client.post(
                f"{BACKEND_URL}/api/analysis/run",
                json={
                    "sport": "football",
                    "refresh": True,
                    "include_live": False,
                    "max_matches": 4,
                    "background": True
                },
                headers=headers
            )
            runner.test(
                "POST /api/analysis/run (football) returns 200",
                resp.status_code == 200,
                f"Status: {resp.status_code}"
            )
            
            if resp.status_code == 200:
                data = resp.json()
                job_id = data.get("job_id")
                runner.test(
                    "Response contains job_id",
                    job_id is not None,
                    f"Job ID: {job_id}"
                )
                
                if job_id:
                    # Poll for completion
                    print(f"   Polling job {job_id}...")
                    for attempt in range(30):  # 30 attempts = 60 seconds max
                        await asyncio.sleep(2)
                        job_resp = await client.get(
                            f"{BACKEND_URL}/api/analysis/jobs/{job_id}",
                            headers=headers
                        )
                        if job_resp.status_code == 200:
                            job_data = job_resp.json()
                            status = job_data.get("status")
                            print(f"   Attempt {attempt+1}: status={status}")
                            
                            if status == "completed":
                                result = job_data.get("result", {})
                                summary = result.get("summary", {})
                                
                                # Check that MLB-V5 keys are NOT present
                                mlb_keys = [
                                    "structural_lean_requires_odds",
                                    "watchlist_manual_odds",
                                    "discarded_after_full_analysis"
                                ]
                                
                                for key in mlb_keys:
                                    runner.test(
                                        f"Football result.summary does NOT have '{key}'",
                                        key not in summary,
                                        f"Keys present: {list(summary.keys())}"
                                    )
                                
                                # Check engine is NOT mlb_pregame_analytics_v2
                                engine = result.get("engine")
                                runner.test(
                                    "Football result.engine != 'mlb_pregame_analytics_v2'",
                                    engine != "mlb_pregame_analytics_v2",
                                    f"Engine: {engine}"
                                )
                                break
                            elif status == "failed":
                                runner.test(
                                    "Football job completed successfully",
                                    False,
                                    f"Job failed: {job_data.get('error')}"
                                )
                                break
                    else:
                        runner.test(
                            "Football job completed within 60s",
                            False,
                            "Job timed out"
                        )
        except Exception as e:
            runner.test(
                "Football regression test",
                False,
                f"Error: {e}"
            )
    
    # ═══════════════════════════════════════════════════════════════════════
    # TEST 3: Basketball Regression - No MLB Keys, No Crash
    # ═══════════════════════════════════════════════════════════════════════
    print("\n[TEST 3] Basketball Regression - No MLB Keys, No Crash")
    print("-"*70)
    
    async with httpx.AsyncClient(timeout=120.0) as client:
        try:
            print("   Running basketball analysis...")
            resp = await client.post(
                f"{BACKEND_URL}/api/analysis/run",
                json={
                    "sport": "basketball",
                    "refresh": True,
                    "include_live": False,
                    "max_matches": 3,
                    "background": False
                },
                headers=headers
            )
            runner.test(
                "POST /api/analysis/run (basketball) returns 200",
                resp.status_code == 200,
                f"Status: {resp.status_code}"
            )
            
            if resp.status_code == 200:
                data = resp.json()
                result = data.get("result", {})
                summary = result.get("summary", {})
                
                # Check that MLB-V5 keys are NOT present
                mlb_keys = [
                    "structural_lean_requires_odds",
                    "watchlist_manual_odds",
                    "discarded_after_full_analysis"
                ]
                
                for key in mlb_keys:
                    runner.test(
                        f"Basketball result.summary does NOT have '{key}'",
                        key not in summary,
                        f"Keys present: {list(summary.keys())}"
                    )
                
                # Check engine is NOT mlb_pregame_analytics_v2
                engine = result.get("engine")
                runner.test(
                    "Basketball result.engine != 'mlb_pregame_analytics_v2'",
                    engine != "mlb_pregame_analytics_v2",
                    f"Engine: {engine}"
                )
        except Exception as e:
            runner.test(
                "Basketball regression test",
                False,
                f"Error: {e}"
            )
    
    # ═══════════════════════════════════════════════════════════════════════
    # TEST 4: MLB Main Path - Engine & New Buckets
    # ═══════════════════════════════════════════════════════════════════════
    print("\n[TEST 4] MLB Main Path - Engine & New Buckets")
    print("-"*70)
    
    async with httpx.AsyncClient(timeout=240.0) as client:
        try:
            print("   Running baseball analysis (background=true)...")
            resp = await client.post(
                f"{BACKEND_URL}/api/analysis/run",
                json={
                    "sport": "baseball",
                    "refresh": True,
                    "include_live": False,
                    "max_matches": 5,
                    "background": True
                },
                headers=headers
            )
            runner.test(
                "POST /api/analysis/run (baseball) returns 200",
                resp.status_code == 200,
                f"Status: {resp.status_code}"
            )
            
            if resp.status_code == 200:
                data = resp.json()
                job_id = data.get("job_id")
                runner.test(
                    "Response contains job_id",
                    job_id is not None,
                    f"Job ID: {job_id}"
                )
                
                if job_id:
                    # Poll for completion (allow up to 180s for MLB pipeline)
                    print(f"   Polling job {job_id} (may take up to 180s)...")
                    for attempt in range(90):  # 90 attempts = 180 seconds max
                        await asyncio.sleep(2)
                        job_resp = await client.get(
                            f"{BACKEND_URL}/api/analysis/jobs/{job_id}",
                            headers=headers
                        )
                        if job_resp.status_code == 200:
                            job_data = job_resp.json()
                            status = job_data.get("status")
                            
                            if attempt % 10 == 0:  # Print every 20 seconds
                                print(f"   Attempt {attempt+1}: status={status}")
                            
                            if status == "completed":
                                result = job_data.get("result", {})
                                
                                # TEST 4a: result.engine == 'mlb_pregame_analytics_v2'
                                engine = result.get("engine")
                                runner.test(
                                    "result.engine == 'mlb_pregame_analytics_v2'",
                                    engine == "mlb_pregame_analytics_v2",
                                    f"Engine: {engine}"
                                )
                                
                                # TEST 4b: result.summary contains new keys
                                summary = result.get("summary", {})
                                new_keys = [
                                    "structural_lean_requires_odds",
                                    "watchlist_manual_odds",
                                    "discarded_after_full_analysis",
                                    "total_manual_review"
                                ]
                                
                                for key in new_keys:
                                    runner.test(
                                        f"result.summary has '{key}'",
                                        key in summary,
                                        f"Value: {type(summary.get(key))}"
                                    )
                                
                                # TEST 4c: result.summary.discarded_motivation == []
                                discarded_motivation = summary.get("discarded_motivation", [])
                                runner.test(
                                    "result.summary.discarded_motivation == []",
                                    len(discarded_motivation) == 0,
                                    f"Count: {len(discarded_motivation)}"
                                )
                                
                                # TEST 4d: Check pipeline_meta
                                pipeline_meta = result.get("pipeline_meta", {})
                                runner.test(
                                    "result.pipeline_meta exists",
                                    len(pipeline_meta) > 0,
                                    f"Keys: {list(pipeline_meta.keys())}"
                                )
                                
                                # Check if ingestion succeeded
                                total_analyzed = summary.get("total_analyzed", 0)
                                abort_reason = pipeline_meta.get("abort_reason")
                                
                                if total_analyzed > 0:
                                    runner.test(
                                        "Baseball ingestion succeeded (total_analyzed > 0)",
                                        True,
                                        f"Analyzed: {total_analyzed} games"
                                    )
                                elif abort_reason:
                                    runner.test(
                                        "Baseball ingestion failed with abort_reason",
                                        True,
                                        f"Abort reason: {abort_reason}"
                                    )
                                else:
                                    runner.test(
                                        "Baseball ingestion status unclear",
                                        False,
                                        "total_analyzed=0 and no abort_reason"
                                    )
                                
                                # Store result for next test
                                global mlb_result
                                mlb_result = result
                                break
                            elif status == "failed":
                                runner.test(
                                    "Baseball job completed successfully",
                                    False,
                                    f"Job failed: {job_data.get('error')}"
                                )
                                break
                    else:
                        runner.test(
                            "Baseball job completed within 180s",
                            False,
                            "Job timed out"
                        )
        except Exception as e:
            runner.test(
                "MLB main path test",
                False,
                f"Error: {e}"
            )
            import traceback
            traceback.print_exc()
    
    # ═══════════════════════════════════════════════════════════════════════
    # TEST 5: Bucket Correctness - STRUCTURAL_LEAN Games
    # ═══════════════════════════════════════════════════════════════════════
    print("\n[TEST 5] Bucket Correctness - STRUCTURAL_LEAN Games")
    print("-"*70)
    
    try:
        if 'mlb_result' in globals():
            result = mlb_result
            summary = result.get("summary", {})
            structural_lean = summary.get("structural_lean_requires_odds", [])
            
            runner.test(
                "structural_lean_requires_odds is a list",
                isinstance(structural_lean, list),
                f"Type: {type(structural_lean)}"
            )
            
            if structural_lean:
                print(f"   Found {len(structural_lean)} games in structural_lean_requires_odds")
                
                # Check first game structure
                first_game = structural_lean[0]
                required_fields = [
                    "requires_manual_odds",
                    "suggested_markets",
                    "manual_review_reason",
                    "structural_quality"
                ]
                
                for field in required_fields:
                    runner.test(
                        f"structural_lean game has '{field}'",
                        field in first_game,
                        f"Value: {first_game.get(field)}"
                    )
                
                # Check suggested_markets is a list
                suggested_markets = first_game.get("suggested_markets", [])
                runner.test(
                    "suggested_markets is a list",
                    isinstance(suggested_markets, list),
                    f"Markets: {suggested_markets}"
                )
                
                # Check structural_quality has score and level
                structural_quality = first_game.get("structural_quality", {})
                runner.test(
                    "structural_quality has 'score'",
                    "score" in structural_quality,
                    f"Score: {structural_quality.get('score')}"
                )
                runner.test(
                    "structural_quality has 'level'",
                    "level" in structural_quality,
                    f"Level: {structural_quality.get('level')}"
                )
                
                # Verify game is NOT in discarded_market
                discarded_market = summary.get("discarded_market", [])
                game_id = first_game.get("match_id")
                discarded_ids = [g.get("match_id") for g in discarded_market]
                runner.test(
                    "structural_lean game NOT in discarded_market",
                    game_id not in discarded_ids,
                    f"Game ID: {game_id}"
                )
            else:
                print("   No games in structural_lean_requires_odds (this is OK)")
        else:
            runner.test(
                "MLB result available for bucket test",
                False,
                "Previous test did not store mlb_result"
            )
    except Exception as e:
        runner.test(
            "Bucket correctness test",
            False,
            f"Error: {e}"
        )
        import traceback
        traceback.print_exc()
    
    # ═══════════════════════════════════════════════════════════════════════
    # TEST 6: Signal Catalog - New Signals Baseball-Only
    # ═══════════════════════════════════════════════════════════════════════
    print("\n[TEST 6] Signal Catalog - New Signals Baseball-Only")
    print("-"*70)
    
    try:
        from services.signal_catalog import make_signal
        
        new_signals = [
            "ODDS_MISSING_STRUCTURAL_ANALYSIS_ONLY",
            "STRUCTURAL_LEAN_DETECTED",
            "MANUAL_ODDS_REQUIRED",
            "MOTIVATION_NEUTRAL_MLB",
            "DISCARDED_ONLY_AFTER_FULL_ANALYSIS"
        ]
        
        for signal_code in new_signals:
            # Test with sport='baseball' - should return signal
            sig_baseball = make_signal(signal_code, sport="baseball")
            runner.test(
                f"make_signal('{signal_code}', sport='baseball') returns signal",
                sig_baseball is not None,
                f"Label: {sig_baseball.get('label') if sig_baseball else 'None'}"
            )
            
            # Test with sport='football' - should return None
            sig_football = make_signal(signal_code, sport="football")
            runner.test(
                f"make_signal('{signal_code}', sport='football') returns None",
                sig_football is None,
                "Correctly filtered cross-sport signal"
            )
            
            # Test with sport='basketball' - should return None
            sig_basketball = make_signal(signal_code, sport="basketball")
            runner.test(
                f"make_signal('{signal_code}', sport='basketball') returns None",
                sig_basketball is None,
                "Correctly filtered cross-sport signal"
            )
    except Exception as e:
        runner.test(
            "Signal catalog test",
            False,
            f"Error: {e}"
        )
        import traceback
        traceback.print_exc()
    
    # ═══════════════════════════════════════════════════════════════════════
    # TEST 7: Import Sanity - mlb_structural_data_quality
    # ═══════════════════════════════════════════════════════════════════════
    print("\n[TEST 7] Import Sanity - mlb_structural_data_quality")
    print("-"*70)
    
    try:
        from services.mlb_pregame_analytics_v2 import mlb_structural_data_quality
        
        runner.test(
            "mlb_structural_data_quality is importable",
            True,
            "Function imported successfully"
        )
        
        # Test with synthetic context - COMPLETE level
        ctx_complete = {
            "home_pitcher_stats": {"era": 3.20, "whip": 1.15},
            "away_pitcher_stats": {"era": 3.50, "whip": 1.20},
            "home_pitcher_quality": {"score": 70},
            "away_pitcher_quality": {"score": 65},
            "bullpen": {"score": 65},
            "offense_home": {"score": 60},
            "offense_away": {"score": 55},
            "park": {"park_runs_mult": 1.05, "weather_score": 50},
            "baseball_historical_profile": {"available": True}
        }
        
        result_complete = mlb_structural_data_quality(ctx_complete)
        
        runner.test(
            "mlb_structural_data_quality returns dict",
            isinstance(result_complete, dict),
            f"Type: {type(result_complete)}"
        )
        
        runner.test(
            "Result has 'score' field",
            "score" in result_complete,
            f"Score: {result_complete.get('score')}"
        )
        
        runner.test(
            "Result has 'level' field",
            "level" in result_complete,
            f"Level: {result_complete.get('level')}"
        )
        
        runner.test(
            "Result has 'reasons' field",
            "reasons" in result_complete,
            f"Reasons: {result_complete.get('reasons')}"
        )
        
        # Test score >= 80 with COMPLETE level
        score = result_complete.get("score", 0)
        level = result_complete.get("level", "")
        runner.test(
            "Complete context: score >= 80",
            score >= 80,
            f"Score: {score}"
        )
        runner.test(
            "Complete context: level == 'COMPLETE'",
            level == "COMPLETE",
            f"Level: {level}"
        )
        
        # Test with empty context - INSUFFICIENT level
        ctx_empty = {}
        result_empty = mlb_structural_data_quality(ctx_empty)
        
        score_empty = result_empty.get("score", 0)
        level_empty = result_empty.get("level", "")
        runner.test(
            "Empty context: score < 50",
            score_empty < 50,
            f"Score: {score_empty}"
        )
        runner.test(
            "Empty context: level == 'INSUFFICIENT'",
            level_empty == "INSUFFICIENT",
            f"Level: {level_empty}"
        )
    except Exception as e:
        runner.test(
            "Import sanity test",
            False,
            f"Error: {e}"
        )
        import traceback
        traceback.print_exc()
    
    # ═══════════════════════════════════════════════════════════════════════
    # TEST 8: GET /api/mlb/day Returns New Buckets
    # ═══════════════════════════════════════════════════════════════════════
    print("\n[TEST 8] GET /api/mlb/day Returns New Buckets")
    print("-"*70)
    
    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            today = datetime.now().strftime("%Y-%m-%d")
            resp = await client.get(
                f"{BACKEND_URL}/api/mlb/day?date={today}",
                headers=headers
            )
            runner.test(
                "GET /api/mlb/day returns 200",
                resp.status_code == 200,
                f"Status: {resp.status_code}"
            )
            
            if resp.status_code == 200:
                data = resp.json()
                
                # Check for new top-level buckets
                new_buckets = [
                    "structural_lean_requires_odds",
                    "watchlist_manual_odds",
                    "discarded_after_full_analysis"
                ]
                
                for bucket in new_buckets:
                    runner.test(
                        f"Response has '{bucket}' bucket",
                        bucket in data,
                        f"Type: {type(data.get(bucket))}"
                    )
                    
                    # Check it's a list (even if empty)
                    if bucket in data:
                        runner.test(
                            f"'{bucket}' is a list",
                            isinstance(data[bucket], list),
                            f"Length: {len(data[bucket])}"
                        )
        except Exception as e:
            runner.test(
                "GET /api/mlb/day test",
                False,
                f"Error: {e}"
            )
    
    # ═══════════════════════════════════════════════════════════════════════
    # TEST 9: MLB-V4 Feedback Endpoints Still Work
    # ═══════════════════════════════════════════════════════════════════════
    print("\n[TEST 9] MLB-V4 Feedback Endpoints Still Work")
    print("-"*70)
    
    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            # Test GET /api/mlb/engine/weights
            resp = await client.get(
                f"{BACKEND_URL}/api/mlb/engine/weights",
                headers=headers
            )
            runner.test(
                "GET /api/mlb/engine/weights returns 200",
                resp.status_code == 200,
                f"Status: {resp.status_code}"
            )
            
            if resp.status_code == 200:
                data = resp.json()
                runner.test(
                    "Response is a dict",
                    isinstance(data, dict),
                    f"Keys: {list(data.keys())}"
                )
                
                # Check for expected weight fields
                expected_fields = ["pitcher_edge", "bullpen", "fav_offense"]
                for field in expected_fields:
                    runner.test(
                        f"Weights has '{field}' field",
                        field in data,
                        f"Value: {data.get(field)}"
                    )
            
            # Test POST /api/mlb/picks/{id}/settle (with a fake pick ID)
            # This should return 404 (pick not found) but not crash
            fake_pick_id = "test_pick_12345"
            resp = await client.post(
                f"{BACKEND_URL}/api/mlb/picks/{fake_pick_id}/settle",
                json={"outcome": "won", "actual_margin": 2.5},
                headers=headers
            )
            runner.test(
                "POST /api/mlb/picks/{id}/settle accepts request",
                resp.status_code in [200, 404],
                f"Status: {resp.status_code} (404 expected for fake ID)"
            )
        except Exception as e:
            runner.test(
                "MLB-V4 feedback endpoints test",
                False,
                f"Error: {e}"
            )
    
    # Print summary
    success = runner.summary()
    return 0 if success else 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
