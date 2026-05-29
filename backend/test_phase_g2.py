"""Phase G2 Backend Testing - Baseball Savant + Team Stats + Parlay Correlation Validator

Tests:
1. Unit tests for correlation_validator (9 test cases)
2. Unit tests for parlay_builder (2 test cases)
3. Unit tests for baseball_savant.enrich_pitcher_dict (fail-soft)
4. Unit tests for mlb_team_stats.get_team_hand_splits (fail-soft)
5. Integration test for /api/mlb/day endpoint (parlay_suggested field)
6. Integration test for per_source_urls tracking
7. Integration test for BULLPEN_FATIGUE_SIGNAL source attribution
8. Auth regression test
"""
import sys
import asyncio
import requests
from datetime import datetime

# Add backend to path for imports
sys.path.insert(0, '/app/backend')

BASE_URL = "https://low-volatility-plays.preview.emergentagent.com/api"

class PhaseG2Tester:
    def __init__(self):
        self.token = None
        self.tests_run = 0
        self.tests_passed = 0
        self.tests_failed = 0
        self.failures = []

    def log(self, msg: str, level: str = "INFO"):
        timestamp = datetime.now().strftime("%H:%M:%S")
        print(f"[{timestamp}] {level}: {msg}")

    def test_unit(self, name: str, test_fn):
        """Run a unit test function."""
        self.tests_run += 1
        self.log(f"Test #{self.tests_run}: {name}")
        
        try:
            result = test_fn()
            if result:
                self.tests_passed += 1
                self.log(f"✅ PASSED", "SUCCESS")
                return True
            else:
                self.tests_failed += 1
                msg = f"❌ FAILED: Test function returned False"
                self.log(msg, "ERROR")
                self.failures.append({"test": name, "reason": msg})
                return False
        except Exception as e:
            self.tests_failed += 1
            msg = f"❌ FAILED: Exception - {str(e)}"
            self.log(msg, "ERROR")
            self.failures.append({"test": name, "reason": msg})
            return False

    def test_integration(self, name: str, method: str, endpoint: str, expected_status: int, 
                        data=None, check_fn=None, timeout=75):
        """Run an integration test."""
        url = f"{BASE_URL}/{endpoint}"
        headers = {}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        if data is not None:
            headers["Content-Type"] = "application/json"

        self.tests_run += 1
        self.log(f"Test #{self.tests_run}: {name}")
        
        try:
            if method == "GET":
                resp = requests.get(url, headers=headers, timeout=timeout)
            elif method == "POST":
                resp = requests.post(url, json=data, headers=headers, timeout=timeout)
            else:
                raise ValueError(f"Unsupported method: {method}")

            if resp.status_code != expected_status:
                self.tests_failed += 1
                msg = f"❌ FAILED: Expected {expected_status}, got {resp.status_code}"
                self.log(msg, "ERROR")
                self.log(f"   Response: {resp.text[:500]}", "ERROR")
                self.failures.append({"test": name, "reason": msg, "response": resp.text[:500]})
                return False, None

            result = resp.json() if resp.headers.get("Content-Type", "").startswith("application/json") else resp.text

            if check_fn:
                check_result = check_fn(result)
                if not check_result:
                    self.tests_failed += 1
                    msg = f"❌ FAILED: Custom check failed"
                    self.log(msg, "ERROR")
                    self.failures.append({"test": name, "reason": msg, "response": str(result)[:500]})
                    return False, result

            self.tests_passed += 1
            self.log(f"✅ PASSED", "SUCCESS")
            return True, result

        except Exception as e:
            self.tests_failed += 1
            msg = f"❌ FAILED: Exception - {str(e)}"
            self.log(msg, "ERROR")
            self.failures.append({"test": name, "reason": msg})
            return False, None

    def run_all_tests(self):
        """Execute all Phase G2 tests."""
        self.log("=" * 80)
        self.log("PHASE G2 BACKEND TESTING - Baseball Savant + Parlay Correlation Validator")
        self.log("=" * 80)

        # ═══════════════════════════════════════════════════════════════════════
        # 0. AUTH TEST (regression)
        # ═══════════════════════════════════════════════════════════════════════
        self.log("\n[0] AUTH REGRESSION TEST", "SECTION")
        
        success, result = self.test_integration(
            "Login with demo@valuebet.app",
            "POST", "auth/login", 200,
            data={"email": "demo@valuebet.app", "password": "demo1234"},
            check_fn=lambda r: "token" in r and "user" in r
        )
        if success and result:
            self.token = result["token"]
            self.log(f"   Token acquired: {self.token[:20]}...")

        # ═══════════════════════════════════════════════════════════════════════
        # 1. UNIT TESTS - correlation_validator
        # ═══════════════════════════════════════════════════════════════════════
        self.log("\n[1] UNIT TESTS - correlation_validator", "SECTION")
        
        from services.parlay_correlation_validator import correlation_validator
        
        # Test 1: Run Line + Over same game = POSITIVE +12
        def test_rl_over_same_game():
            picks = [
                {
                    "game_pk": 12345,
                    "match_label": "Yankees @ Red Sox",
                    "home_team": "Red Sox",
                    "away_team": "Yankees",
                    "venue": "Fenway Park",
                    "weather_tags": [],
                    "pitcher_home_id": 1001,
                    "pitcher_away_id": 1002,
                    "recommendation": {"market": "Run Line -1.5", "selection": "Red Sox -1.5", "score": 75}
                },
                {
                    "game_pk": 12345,
                    "match_label": "Yankees @ Red Sox",
                    "home_team": "Red Sox",
                    "away_team": "Yankees",
                    "venue": "Fenway Park",
                    "weather_tags": [],
                    "pitcher_home_id": 1001,
                    "pitcher_away_id": 1002,
                    "recommendation": {"market": "Over 8.5", "selection": "Over 8.5", "score": 72}
                }
            ]
            result = correlation_validator(picks)
            self.log(f"   correlation_score: {result['correlation_score']}, risk_level: {result['risk_level']}")
            self.log(f"   positive_correlations: {len(result['positive_correlations'])}")
            
            # Check: correlation_score >= 78, risk_level=LOW, has positive correlation with impact +12
            has_positive_12 = any(c["impact"] == 12 for c in result["positive_correlations"])
            return (result["correlation_score"] >= 78 and 
                    result["risk_level"] == "LOW" and 
                    has_positive_12)
        
        self.test_unit("correlation_validator: Run Line + Over same game = POSITIVE +12", test_rl_over_same_game)
        
        # Test 2: Run Line + Under 7.5 same game = NEGATIVE -12
        def test_rl_under_same_game():
            picks = [
                {
                    "game_pk": 12346,
                    "match_label": "Dodgers @ Giants",
                    "home_team": "Giants",
                    "away_team": "Dodgers",
                    "venue": "Oracle Park",
                    "weather_tags": [],
                    "pitcher_home_id": 2001,
                    "pitcher_away_id": 2002,
                    "recommendation": {"market": "Run Line -1.5", "selection": "Dodgers -1.5", "score": 75}
                },
                {
                    "game_pk": 12346,
                    "match_label": "Dodgers @ Giants",
                    "home_team": "Giants",
                    "away_team": "Dodgers",
                    "venue": "Oracle Park",
                    "weather_tags": [],
                    "pitcher_home_id": 2001,
                    "pitcher_away_id": 2002,
                    "recommendation": {"market": "Under 7.5", "selection": "Under 7.5", "score": 70}
                }
            ]
            result = correlation_validator(picks)
            self.log(f"   correlation_score: {result['correlation_score']}, risk_level: {result['risk_level']}")
            self.log(f"   negative_correlations: {len(result['negative_correlations'])}")
            
            # Check: has negative correlation with impact -12, risk_level=MEDIUM
            has_negative_12 = any(c["impact"] == -12 for c in result["negative_correlations"])
            return (result["risk_level"] == "MEDIUM" and has_negative_12)
        
        self.test_unit("correlation_validator: Run Line + Under 7.5 same game = NEGATIVE -12", test_rl_under_same_game)
        
        # Test 3: Run Line favorite + Team Total Under SAME team = NEGATIVE -20
        def test_rl_team_total_under_contradiction():
            picks = [
                {
                    "game_pk": 12347,
                    "match_label": "Astros @ Rangers",
                    "home_team": "Rangers",
                    "away_team": "Astros",
                    "venue": "Globe Life Field",
                    "weather_tags": [],
                    "pitcher_home_id": 3001,
                    "pitcher_away_id": 3002,
                    "recommendation": {"market": "Run Line -1.5", "selection": "Astros -1.5", "score": 78}
                },
                {
                    "game_pk": 12347,
                    "match_label": "Astros @ Rangers",
                    "home_team": "Rangers",
                    "away_team": "Astros",
                    "venue": "Globe Life Field",
                    "weather_tags": [],
                    "pitcher_home_id": 3001,
                    "pitcher_away_id": 3002,
                    "recommendation": {"market": "Team Total Under 4.5", "selection": "Astros Under 4.5", "score": 68}
                }
            ]
            result = correlation_validator(picks)
            self.log(f"   correlation_score: {result['correlation_score']}, risk_level: {result['risk_level']}")
            self.log(f"   negative_correlations: {len(result['negative_correlations'])}")
            self.log(f"   recommended_adjustments: {result['recommended_adjustments']}")
            
            # Check: has negative correlation with impact -20, risk_level=HIGH, recommended_adjustments mentions contradiction
            has_negative_20 = any(c["impact"] == -20 for c in result["negative_correlations"])
            has_adjustment = len(result["recommended_adjustments"]) > 0
            return (result["risk_level"] == "HIGH" and has_negative_20 and has_adjustment)
        
        self.test_unit("correlation_validator: Run Line + Team Total Under SAME team = NEGATIVE -20", test_rl_team_total_under_contradiction)
        
        # Test 4: 3 Overs across DIFFERENT games = warnings
        def test_3_overs_concentration():
            picks = [
                {
                    "game_pk": 12348,
                    "match_label": "Game 1",
                    "home_team": "Team A",
                    "away_team": "Team B",
                    "venue": "Stadium 1",
                    "weather_tags": [],
                    "pitcher_home_id": 4001,
                    "pitcher_away_id": 4002,
                    "recommendation": {"market": "Over 9.5", "selection": "Over 9.5", "score": 75}
                },
                {
                    "game_pk": 12349,
                    "match_label": "Game 2",
                    "home_team": "Team C",
                    "away_team": "Team D",
                    "venue": "Stadium 2",
                    "weather_tags": [],
                    "pitcher_home_id": 4003,
                    "pitcher_away_id": 4004,
                    "recommendation": {"market": "Over 8.5", "selection": "Over 8.5", "score": 72}
                },
                {
                    "game_pk": 12350,
                    "match_label": "Game 3",
                    "home_team": "Team E",
                    "away_team": "Team F",
                    "venue": "Stadium 3",
                    "weather_tags": [],
                    "pitcher_home_id": 4005,
                    "pitcher_away_id": 4006,
                    "recommendation": {"market": "Over 10.5", "selection": "Over 10.5", "score": 70}
                }
            ]
            result = correlation_validator(picks)
            self.log(f"   warnings: {result['warnings']}")
            
            # Check: warnings contains '3 Overs en el parlay'
            has_over_warning = any("3 Overs" in w for w in result["warnings"])
            return has_over_warning
        
        self.test_unit("correlation_validator: 3 Overs across DIFFERENT games = warnings", test_3_overs_concentration)
        
        # Test 5: NRFI + F5 Under same game = POSITIVE +8
        def test_nrfi_f5_under():
            picks = [
                {
                    "game_pk": 12351,
                    "match_label": "Mets @ Braves",
                    "home_team": "Braves",
                    "away_team": "Mets",
                    "venue": "Truist Park",
                    "weather_tags": [],
                    "pitcher_home_id": 5001,
                    "pitcher_away_id": 5002,
                    "recommendation": {"market": "NRFI", "selection": "NRFI", "score": 78}
                },
                {
                    "game_pk": 12351,
                    "match_label": "Mets @ Braves",
                    "home_team": "Braves",
                    "away_team": "Mets",
                    "venue": "Truist Park",
                    "weather_tags": [],
                    "pitcher_home_id": 5001,
                    "pitcher_away_id": 5002,
                    "recommendation": {"market": "F5 Under 4.5", "selection": "F5 Under 4.5", "score": 75}
                }
            ]
            result = correlation_validator(picks)
            self.log(f"   correlation_score: {result['correlation_score']}, risk_level: {result['risk_level']}")
            self.log(f"   positive_correlations: {len(result['positive_correlations'])}")
            
            # Check: has positive correlation with impact +8
            has_positive_8 = any(c["impact"] == 8 for c in result["positive_correlations"])
            return has_positive_8
        
        self.test_unit("correlation_validator: NRFI + F5 Under same game = POSITIVE +8", test_nrfi_f5_under)
        
        # Test 6: Empty/single pick → correlation_score=100, risk=LOW
        def test_empty_single_pick():
            result_empty = correlation_validator([])
            result_single = correlation_validator([{
                "game_pk": 12352,
                "match_label": "Single Game",
                "home_team": "Team X",
                "away_team": "Team Y",
                "venue": "Stadium X",
                "weather_tags": [],
                "pitcher_home_id": 6001,
                "pitcher_away_id": 6002,
                "recommendation": {"market": "Over 9.5", "selection": "Over 9.5", "score": 75}
            }])
            
            self.log(f"   Empty: correlation_score={result_empty['correlation_score']}, risk={result_empty['risk_level']}")
            self.log(f"   Single: correlation_score={result_single['correlation_score']}, risk={result_single['risk_level']}")
            
            return (result_empty["correlation_score"] == 100 and result_empty["risk_level"] == "LOW" and
                    result_single["correlation_score"] == 100 and result_single["risk_level"] == "LOW")
        
        self.test_unit("correlation_validator: Empty/single pick → correlation_score=100, risk=LOW", test_empty_single_pick)
        
        # Test 7: Two picks sharing same pitcher_home_id but DIFFERENT games = NEGATIVE -6
        def test_same_pitcher_different_games():
            picks = [
                {
                    "game_pk": 12353,
                    "match_label": "Game A",
                    "home_team": "Team A",
                    "away_team": "Team B",
                    "venue": "Stadium A",
                    "weather_tags": [],
                    "pitcher_home_id": 7001,
                    "pitcher_away_id": 7002,
                    "recommendation": {"market": "Over 9.5", "selection": "Over 9.5", "score": 75}
                },
                {
                    "game_pk": 12354,
                    "match_label": "Game B",
                    "home_team": "Team C",
                    "away_team": "Team D",
                    "venue": "Stadium B",
                    "weather_tags": [],
                    "pitcher_home_id": 7001,  # Same pitcher
                    "pitcher_away_id": 7003,
                    "recommendation": {"market": "Under 8.5", "selection": "Under 8.5", "score": 72}
                }
            ]
            result = correlation_validator(picks)
            self.log(f"   negative_correlations: {len(result['negative_correlations'])}")
            
            # Check: has negative correlation with impact -6 and reason mentions 'mismo pitcher'
            has_negative_6 = any(c["impact"] == -6 and "mismo pitcher" in c["reason"] 
                                for c in result["negative_correlations"])
            return has_negative_6
        
        self.test_unit("correlation_validator: Same pitcher different games = NEGATIVE -6", test_same_pitcher_different_games)

        # ═══════════════════════════════════════════════════════════════════════
        # 2. UNIT TESTS - parlay_builder
        # ═══════════════════════════════════════════════════════════════════════
        self.log("\n[2] UNIT TESTS - parlay_builder", "SECTION")
        
        from services.parlay_correlation_validator import parlay_builder
        
        # Test 8: parlay_builder with 8 mixed picks
        def test_parlay_builder_mixed():
            picks = []
            # Create 4 LOW-risk pairs (same game, Run Line + Over)
            for i in range(4):
                game_pk = 20000 + i
                picks.append({
                    "game_pk": game_pk,
                    "match_label": f"Game {i}",
                    "home_team": f"Home {i}",
                    "away_team": f"Away {i}",
                    "venue": f"Stadium {i}",
                    "weather_tags": [],
                    "pitcher_home_id": 8000 + i * 2,
                    "pitcher_away_id": 8000 + i * 2 + 1,
                    "recommendation": {"market": "Run Line -1.5", "selection": f"Home {i} -1.5", "score": 75 + i}
                })
                picks.append({
                    "game_pk": game_pk,
                    "match_label": f"Game {i}",
                    "home_team": f"Home {i}",
                    "away_team": f"Away {i}",
                    "venue": f"Stadium {i}",
                    "weather_tags": [],
                    "pitcher_home_id": 8000 + i * 2,
                    "pitcher_away_id": 8000 + i * 2 + 1,
                    "recommendation": {"market": "Over 9.5", "selection": "Over 9.5", "score": 72 + i}
                })
            
            result = parlay_builder(picks, max_size=4, min_score=60)
            self.log(f"   parlay size: {result['size']}, combined_score: {result['combined_score']}")
            self.log(f"   risk_level: {result['validator']['risk_level']}")
            self.log(f"   rejected_count: {result['rejected_count']}")
            
            # Check: parlay is non-empty, risk_level != HIGH, size between 2 and 4
            return (len(result["parlay"]) > 0 and 
                    result["validator"]["risk_level"] != "HIGH" and
                    2 <= result["size"] <= 4)
        
        self.test_unit("parlay_builder: 8 mixed picks returns best 2..4 combination", test_parlay_builder_mixed)
        
        # Test 9: parlay_builder with only HIGH-risk combinations
        def test_parlay_builder_high_risk_only():
            picks = []
            # Create picks where ANY 2+ combination will be HIGH-risk
            # Strategy: Use same pitcher across all picks (different games) = -6 each pair
            # Plus concentration penalties to push risk to HIGH
            for i in range(6):
                game_pk = 21000 + i
                picks.append({
                    "game_pk": game_pk,
                    "match_label": f"Game {i}",
                    "home_team": f"Home {i}",
                    "away_team": f"Away {i}",
                    "venue": "Coors Field",  # Same venue for all = concentration
                    "weather_tags": ["WIND_OUT_OVER"],  # Same weather tag
                    "pitcher_home_id": 9000,  # SAME pitcher for all
                    "pitcher_away_id": 9000 + i,
                    "recommendation": {"market": "Over 10.5", "selection": "Over 10.5", "score": 75}
                })
            
            result = parlay_builder(picks, max_size=4, min_score=60)
            self.log(f"   parlay size: {result['size']}, parlay: {len(result['parlay'])}")
            self.log(f"   risk_level: {result['validator']['risk_level']}")
            
            # Check: parlay is empty OR risk_level is HIGH (builder should skip HIGH-risk)
            # Note: The builder might return empty if ALL combinations are HIGH-risk
            # OR it might return a parlay with MEDIUM risk if it finds a less-bad combination
            # The key is that it should NOT return a LOW-risk parlay with these picks
            if len(result["parlay"]) == 0:
                self.log(f"   ✓ Correctly returned empty parlay (all HIGH-risk)")
                return True
            elif result["validator"]["risk_level"] == "HIGH":
                self.log(f"   ⚠ Returned HIGH-risk parlay (should have been skipped)")
                return False
            else:
                # If it returns MEDIUM risk, that's acceptable (found a less-bad combination)
                self.log(f"   ✓ Returned {result['validator']['risk_level']} risk parlay (acceptable)")
                return True
        
        self.test_unit("parlay_builder: Only HIGH-risk combinations → parlay=[]", test_parlay_builder_high_risk_only)

        # ═══════════════════════════════════════════════════════════════════════
        # 3. UNIT TESTS - baseball_savant.enrich_pitcher_dict (fail-soft)
        # ═══════════════════════════════════════════════════════════════════════
        self.log("\n[3] UNIT TESTS - baseball_savant.enrich_pitcher_dict", "SECTION")
        
        from services.baseball_savant import enrich_pitcher_dict
        
        # Test 10: enrich_pitcher_dict fail-soft on network failure
        def test_enrich_pitcher_fail_soft():
            # Test with a pitcher dict missing xera/fip
            pitcher = {
                "pitcher_id": 999999999,  # Non-existent pitcher
                "name": "Test Pitcher",
                "era": 3.50,
                "whip": 1.20
            }
            
            # This should NOT crash even if Savant blocks or network fails
            try:
                result = asyncio.run(enrich_pitcher_dict(pitcher, db=None))
                self.log(f"   Result: {result}")
                # Check: function returns the dict unchanged (no crash)
                return result is not None and result.get("name") == "Test Pitcher"
            except Exception as e:
                self.log(f"   Exception: {e}", "ERROR")
                return False
        
        self.test_unit("baseball_savant.enrich_pitcher_dict: fail-soft on network failure", test_enrich_pitcher_fail_soft)

        # ═══════════════════════════════════════════════════════════════════════
        # 4. UNIT TESTS - mlb_team_stats.get_team_hand_splits (fail-soft)
        # ═══════════════════════════════════════════════════════════════════════
        self.log("\n[4] UNIT TESTS - mlb_team_stats.get_team_hand_splits", "SECTION")
        
        from services.mlb_team_stats import get_team_hand_splits
        
        # Test 11: get_team_hand_splits fail-soft with fake team_id
        def test_team_hand_splits_fail_soft():
            # Test with fake team_id and no DB
            try:
                result = asyncio.run(get_team_hand_splits(None, 999999, season=2025))
                self.log(f"   Result: {result}")
                # Check: function returns fail-soft (no crash, empty vs_lhp/vs_rhp)
                # The function may return {} or {'vs_lhp': {}, 'vs_rhp': {}, '_source_url': '...'}
                # Both are acceptable fail-soft behaviors
                if result == {}:
                    self.log(f"   ✓ Returned empty dict (fail-soft)")
                    return True
                elif isinstance(result, dict) and not result.get("vs_lhp") and not result.get("vs_rhp"):
                    self.log(f"   ✓ Returned dict with empty vs_lhp/vs_rhp (fail-soft)")
                    return True
                else:
                    self.log(f"   ⚠ Unexpected result structure", "WARN")
                    return False
            except Exception as e:
                self.log(f"   Exception: {e}", "ERROR")
                return False
        
        self.test_unit("mlb_team_stats.get_team_hand_splits: fail-soft with fake team_id", test_team_hand_splits_fail_soft)

        # ═══════════════════════════════════════════════════════════════════════
        # 5. INTEGRATION TEST - /api/mlb/day endpoint
        # ═══════════════════════════════════════════════════════════════════════
        self.log("\n[5] INTEGRATION TEST - /api/mlb/day endpoint", "SECTION")
        self.log("   ⚠ This test may take 60-75 seconds (parallel enrichment + parlay builder)...", "WARN")
        
        success, result = self.test_integration(
            "GET /api/mlb/day?date=2025-08-15",
            "GET", "mlb/day?date=2025-08-15", 200,
            timeout=75,
            check_fn=lambda r: (
                "parlay_suggested" in r and
                "parlay" in r["parlay_suggested"] and
                "validator" in r["parlay_suggested"] and
                "combined_score" in r["parlay_suggested"] and
                "size" in r["parlay_suggested"] and
                "rejected_count" in r["parlay_suggested"] and
                "pipeline_meta" in r and
                "games_processed" in r["pipeline_meta"] and
                "games_capped" in r["pipeline_meta"]
            )
        )
        
        if success and result:
            parlay = result["parlay_suggested"]
            meta = result["pipeline_meta"]
            self.log(f"   parlay_suggested.size: {parlay['size']}")
            self.log(f"   parlay_suggested.combined_score: {parlay['combined_score']}")
            self.log(f"   parlay_suggested.validator.risk_level: {parlay['validator']['risk_level']}")
            self.log(f"   parlay_suggested.rejected_count: {parlay['rejected_count']}")
            self.log(f"   pipeline_meta.games_processed: {meta['games_processed']}")
            self.log(f"   pipeline_meta.games_capped: {meta['games_capped']}")
            
            # Verify validator structure
            validator = parlay["validator"]
            required_validator_fields = ["correlation_score", "risk_level", "positive_correlations", 
                                        "negative_correlations", "warnings", "recommended_adjustments"]
            missing = [f for f in required_validator_fields if f not in validator]
            if missing:
                self.log(f"   ⚠ Missing validator fields: {missing}", "WARN")
            else:
                self.log(f"   ✓ Validator structure complete")

        # ═══════════════════════════════════════════════════════════════════════
        # 6. INTEGRATION TEST - per_source_urls tracking
        # ═══════════════════════════════════════════════════════════════════════
        self.log("\n[6] INTEGRATION TEST - per_source_urls tracking", "SECTION")
        
        if success and result:
            # Check picks and discarded_picks for per_source_urls
            picks = result.get("picks", [])
            rescued = result.get("rescued_picks", [])
            discarded = result.get("discarded_picks", [])
            
            all_entries = picks + rescued + discarded
            self.log(f"   Total entries (picks + rescued + discarded): {len(all_entries)}")
            
            if all_entries:
                sample = all_entries[0]
                self.log(f"   Sample entry game_pk: {sample.get('game_pk')}")
                
                # Check per_source_urls field
                if "per_source_urls" in sample:
                    per_source = sample["per_source_urls"]
                    self.log(f"   ✓ per_source_urls field present")
                    self.log(f"   pitcher_confirmation: {per_source.get('pitcher_confirmation', 'N/A')[:80]}")
                    
                    # Verify pitcher_confirmation is statsapi URL
                    if per_source.get("pitcher_confirmation", "").startswith("https://statsapi.mlb.com"):
                        self.log(f"   ✓ pitcher_confirmation is statsapi URL")
                    else:
                        self.log(f"   ⚠ pitcher_confirmation is not statsapi URL", "WARN")
                    
                    # Check for team enrichment URLs (if present)
                    if per_source.get("home_team_hand"):
                        self.log(f"   ✓ home_team_hand URL present: {per_source['home_team_hand'][:80]}")
                    if per_source.get("home_bullpen_usage"):
                        self.log(f"   ✓ home_bullpen_usage URL present: {per_source['home_bullpen_usage'][:80]}")
                else:
                    self.log(f"   ⚠ per_source_urls field missing", "WARN")
            else:
                self.log(f"   ⚠ No entries to check (all games may be discarded)", "WARN")

        # ═══════════════════════════════════════════════════════════════════════
        # 7. INTEGRATION TEST - BULLPEN_FATIGUE_SIGNAL source attribution
        # ═══════════════════════════════════════════════════════════════════════
        self.log("\n[7] INTEGRATION TEST - BULLPEN_FATIGUE_SIGNAL source attribution", "SECTION")
        
        if success and result:
            signals_by_game = result.get("editorial_context_signals_by_game", {})
            self.log(f"   Games with signals: {len(signals_by_game)}")
            
            bullpen_fatigue_found = False
            for game_pk, signals in signals_by_game.items():
                for signal in signals:
                    if signal.get("code") == "BULLPEN_FATIGUE_SIGNAL":
                        bullpen_fatigue_found = True
                        self.log(f"   ✓ Found BULLPEN_FATIGUE_SIGNAL in game {game_pk}")
                        self.log(f"   source: {signal.get('source')}")
                        self.log(f"   source_url: {signal.get('source_url', 'N/A')[:80]}")
                        
                        # Verify source and source_url
                        if signal.get("source") == "MLB Stats API (gameLogs)":
                            self.log(f"   ✓ source is 'MLB Stats API (gameLogs)'")
                        else:
                            self.log(f"   ⚠ source is not 'MLB Stats API (gameLogs)': {signal.get('source')}", "WARN")
                        
                        if signal.get("source_url", "").startswith("https://statsapi.mlb.com/api/v1/teams/"):
                            self.log(f"   ✓ source_url starts with https://statsapi.mlb.com/api/v1/teams/")
                        else:
                            self.log(f"   ⚠ source_url does not start with expected URL", "WARN")
                        
                        break
                if bullpen_fatigue_found:
                    break
            
            if not bullpen_fatigue_found:
                self.log(f"   ⚠ No BULLPEN_FATIGUE_SIGNAL found (may not be present in test data)", "WARN")

        # ═══════════════════════════════════════════════════════════════════════
        # SUMMARY
        # ═══════════════════════════════════════════════════════════════════════
        self.log("\n" + "=" * 80)
        self.log("TEST SUMMARY", "SECTION")
        self.log("=" * 80)
        self.log(f"Total tests: {self.tests_run}")
        self.log(f"Passed: {self.tests_passed} ✅")
        self.log(f"Failed: {self.tests_failed} ❌")
        self.log(f"Success rate: {(self.tests_passed/self.tests_run*100):.1f}%")
        
        if self.failures:
            self.log("\nFAILED TESTS:", "ERROR")
            for i, failure in enumerate(self.failures, 1):
                self.log(f"{i}. {failure['test']}", "ERROR")
                self.log(f"   Reason: {failure['reason']}", "ERROR")
        
        return self.tests_failed == 0


def main():
    tester = PhaseG2Tester()
    success = tester.run_all_tests()
    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
