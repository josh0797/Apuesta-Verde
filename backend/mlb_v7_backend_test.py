"""
MLB Engine V3 (V7) — Explainability & Game Script Backend Test Suite

Tests all requirements from the MLB-V7 review_request:
1. Pure function unit tests for mlb_pregame_analytics_v3.py
2. Full pipeline integration via POST /api/analysis/run
3. Verification of _mlb_script_v3 structure in picks
4. Baseball-first reasoning (no generic engine phrases)
5. Market diversification tracking
6. No regression in football/basketball pipelines
7. MLB buckets preserved (picks/rescued_picks/structural_lean_requires_odds/watchlist_manual_odds/discarded_after_full_analysis)
"""
import sys
import requests
import json
from datetime import datetime
from typing import Any, Optional

# Public endpoint from frontend/.env
BASE_URL = "https://low-volatility-plays.preview.emergentagent.com"

class Colors:
    GREEN = '\033[92m'
    RED = '\033[91m'
    YELLOW = '\033[93m'
    BLUE = '\033[94m'
    END = '\033[0m'

class MLBv7Tester:
    def __init__(self):
        self.tests_run = 0
        self.tests_passed = 0
        self.tests_failed = 0
        self.token = None
        self.results = []

    def log(self, message: str, level: str = "INFO"):
        prefix = {
            "INFO": f"{Colors.BLUE}ℹ{Colors.END}",
            "PASS": f"{Colors.GREEN}✓{Colors.END}",
            "FAIL": f"{Colors.RED}✗{Colors.END}",
            "WARN": f"{Colors.YELLOW}⚠{Colors.END}",
        }.get(level, "")
        print(f"{prefix} {message}")

    def test(self, name: str, func, *args, **kwargs):
        """Run a single test"""
        self.tests_run += 1
        self.log(f"\n{'='*60}", "INFO")
        self.log(f"Test {self.tests_run}: {name}", "INFO")
        self.log(f"{'='*60}", "INFO")
        try:
            result = func(*args, **kwargs)
            if result:
                self.tests_passed += 1
                self.log(f"PASSED: {name}", "PASS")
                self.results.append({"test": name, "status": "PASS", "error": None})
                return True
            else:
                self.tests_failed += 1
                self.log(f"FAILED: {name}", "FAIL")
                self.results.append({"test": name, "status": "FAIL", "error": "Test returned False"})
                return False
        except Exception as e:
            self.tests_failed += 1
            self.log(f"FAILED: {name} - {str(e)}", "FAIL")
            self.results.append({"test": name, "status": "FAIL", "error": str(e)})
            return False

    def summary(self):
        """Print test summary"""
        self.log(f"\n{'='*60}", "INFO")
        self.log("TEST SUMMARY", "INFO")
        self.log(f"{'='*60}", "INFO")
        self.log(f"Total: {self.tests_run}", "INFO")
        self.log(f"Passed: {self.tests_passed}", "PASS")
        self.log(f"Failed: {self.tests_failed}", "FAIL" if self.tests_failed > 0 else "INFO")
        self.log(f"Success Rate: {(self.tests_passed/self.tests_run*100):.1f}%", "INFO")
        return self.tests_failed == 0

    # ========================================================================
    # 1. PURE FUNCTION UNIT TESTS
    # ========================================================================
    def test_pure_functions_import(self):
        """Test that mlb_pregame_analytics_v3 exposes all required functions"""
        self.log("Testing v3 module imports...", "INFO")
        try:
            sys.path.insert(0, '/app/backend')
            from services import mlb_pregame_analytics_v3 as v3
            
            required = [
                'generate_mlb_game_script',
                'build_pitcher_block',
                'build_why_this_pick',
                'build_confidence_breakdown',
                'generate_baseball_first_reasons',
                'apply_market_diversification',
                'build_v3_payload',
                'SCRIPT_LABELS_ES',
            ]
            
            for func_name in required:
                if not hasattr(v3, func_name):
                    self.log(f"Missing function: {func_name}", "FAIL")
                    return False
                self.log(f"Found: {func_name}", "PASS")
            
            # Check SCRIPT_LABELS_ES has all 8 codes
            expected_codes = [
                "LOW_SCORING_PITCHERS_DUEL",
                "OFFENSIVE_SHOOTOUT",
                "FAVORITE_DOMINANCE",
                "BULLPEN_BATTLE",
                "UNDERDOG_CAN_COMPETE",
                "PITCHER_MISMATCH",
                "HIGH_VARIANCE_GAME",
                "LOW_VARIANCE_GAME",
            ]
            for code in expected_codes:
                if code not in v3.SCRIPT_LABELS_ES:
                    self.log(f"Missing script code: {code}", "FAIL")
                    return False
            
            self.log(f"All 8 script codes present in SCRIPT_LABELS_ES", "PASS")
            return True
        except Exception as e:
            self.log(f"Import failed: {e}", "FAIL")
            return False

    def test_generate_mlb_game_script_pitcher_duel(self):
        """Test generate_mlb_game_script with pitcher duel context"""
        self.log("Testing generate_mlb_game_script (pitcher duel)...", "INFO")
        try:
            sys.path.insert(0, '/app/backend')
            from services.mlb_pregame_analytics_v3 import generate_mlb_game_script
            
            # Pitcher duel: high pitcher quality, low expected runs
            scoring_ctx = {
                "home_pitcher_quality": {"score": 75},
                "away_pitcher_quality": {"score": 70},
                "offense_home": {"score": 45},
                "offense_away": {"score": 40},
                "park": {"park_runs_mult": 0.95, "weather_score": 40},
                "under_profile": {},
            }
            v2_payload = {
                "expectedRuns": 7.2,
                "marginProjection": 0.8,
                "pitcherCentered": {"mismatch": False, "strongEdge": False},
            }
            
            result = generate_mlb_game_script(scoring_ctx, v2_payload)
            
            # Verify structure
            required_keys = ["script_code", "label_es", "narrative_es", "key_drivers", 
                           "expected_runs", "projected_margin", "variance"]
            for key in required_keys:
                if key not in result:
                    self.log(f"Missing key: {key}", "FAIL")
                    return False
            
            # Should classify as LOW_SCORING_PITCHERS_DUEL
            if result["script_code"] != "LOW_SCORING_PITCHERS_DUEL":
                self.log(f"Expected LOW_SCORING_PITCHERS_DUEL, got {result['script_code']}", "FAIL")
                return False
            
            self.log(f"Script code: {result['script_code']}", "PASS")
            self.log(f"Label: {result['label_es']}", "INFO")
            self.log(f"Narrative: {result['narrative_es'][:100]}...", "INFO")
            self.log(f"Key drivers: {result['key_drivers']}", "INFO")
            self.log(f"Variance: {result['variance']}", "INFO")
            
            return True
        except Exception as e:
            self.log(f"Test failed: {e}", "FAIL")
            return False

    def test_generate_mlb_game_script_shootout(self):
        """Test generate_mlb_game_script with offensive shootout context"""
        self.log("Testing generate_mlb_game_script (shootout)...", "INFO")
        try:
            sys.path.insert(0, '/app/backend')
            from services.mlb_pregame_analytics_v3 import generate_mlb_game_script
            
            # Shootout: low pitcher quality, high expected runs, strong offenses
            scoring_ctx = {
                "home_pitcher_quality": {"score": 35},
                "away_pitcher_quality": {"score": 40},
                "offense_home": {"score": 65},
                "offense_away": {"score": 60},
                "park": {"park_runs_mult": 1.08, "weather_score": 70},
                "under_profile": {},
            }
            v2_payload = {
                "expectedRuns": 10.2,
                "marginProjection": 1.2,
                "pitcherCentered": {"mismatch": False, "strongEdge": False},
            }
            
            result = generate_mlb_game_script(scoring_ctx, v2_payload)
            
            # Should classify as OFFENSIVE_SHOOTOUT
            if result["script_code"] != "OFFENSIVE_SHOOTOUT":
                self.log(f"Expected OFFENSIVE_SHOOTOUT, got {result['script_code']}", "FAIL")
                return False
            
            self.log(f"Script code: {result['script_code']}", "PASS")
            self.log(f"Variance: {result['variance']}", "INFO")
            
            return True
        except Exception as e:
            self.log(f"Test failed: {e}", "FAIL")
            return False

    def test_build_pitcher_block(self):
        """Test build_pitcher_block with valid pitcher stats"""
        self.log("Testing build_pitcher_block...", "INFO")
        try:
            sys.path.insert(0, '/app/backend')
            from services.mlb_pregame_analytics_v3 import build_pitcher_block
            
            scoring_ctx = {
                "home_pitcher_stats": {
                    "name": "Gerrit Cole",
                    "xera": 3.25,
                    "fip": 3.10,
                    "era": 3.45,
                    "whip": 1.05,
                    "k_per_9": 11.2,
                    "bb_per_9": 2.1,
                    "throws_hand": "R",
                },
                "away_pitcher_stats": {
                    "name": "Shane Bieber",
                    "xera": 3.15,
                    "fip": 2.95,
                    "era": 3.20,
                    "whip": 0.98,
                    "k_per_9": 12.5,
                    "bb_per_9": 1.8,
                    "throws_hand": "R",
                },
                "home_pitcher_quality": {"score": 72},
                "away_pitcher_quality": {"score": 75},
                "home_team": {"team_name": "Yankees"},
                "away_team": {"team_name": "Guardians"},
            }
            
            result = build_pitcher_block(scoring_ctx)
            
            # Verify structure
            if "home" not in result or "away" not in result:
                self.log("Missing home/away keys", "FAIL")
                return False
            
            # Check home pitcher
            home = result["home"]
            if home.get("name") != "Gerrit Cole":
                self.log(f"Expected Gerrit Cole, got {home.get('name')}", "FAIL")
                return False
            if home.get("qualityScore") != 72:
                self.log(f"Expected qualityScore 72, got {home.get('qualityScore')}", "FAIL")
                return False
            if not home.get("primary_stats"):
                self.log("Missing primary_stats", "FAIL")
                return False
            if len(home["primary_stats"]) < 1 or len(home["primary_stats"]) > 2:
                self.log(f"Expected 1-2 primary_stats, got {len(home['primary_stats'])}", "FAIL")
                return False
            
            # Check away pitcher
            away = result["away"]
            if away.get("name") != "Shane Bieber":
                self.log(f"Expected Shane Bieber, got {away.get('name')}", "FAIL")
                return False
            if away.get("qualityScore") != 75:
                self.log(f"Expected qualityScore 75, got {away.get('qualityScore')}", "FAIL")
                return False
            
            # Check bothConfirmed
            if not result.get("bothConfirmed"):
                self.log("Expected bothConfirmed=True", "FAIL")
                return False
            
            self.log(f"Home pitcher: {home['name']} (Q={home['qualityScore']})", "PASS")
            self.log(f"Home stats: {home['primary_stats']}", "INFO")
            self.log(f"Away pitcher: {away['name']} (Q={away['qualityScore']})", "PASS")
            self.log(f"Away stats: {away['primary_stats']}", "INFO")
            
            return True
        except Exception as e:
            self.log(f"Test failed: {e}", "FAIL")
            return False

    def test_build_why_this_pick(self):
        """Test build_why_this_pick checklist generation"""
        self.log("Testing build_why_this_pick...", "INFO")
        try:
            sys.path.insert(0, '/app/backend')
            from services.mlb_pregame_analytics_v3 import build_why_this_pick
            
            scoring_ctx = {
                "home_pitcher_quality": {"score": 70},
                "away_pitcher_quality": {"score": 65},
                "offense_home": {"score": 55},
                "offense_away": {"score": 50},
                "bullpen": {"score": 62},
                "park": {"park_runs_mult": 1.02, "weather_score": 55},
                "home_pitcher_stats": {"name": "Pitcher A"},
                "away_pitcher_stats": {"name": "Pitcher B"},
                "favorite_bullpen_era_7d": 4.10,
            }
            v2_payload = {
                "expectedRuns": 8.5,
                "recommendedLine": "Under 9.5",
                "edgeVsLine": 1.2,
            }
            
            result = build_why_this_pick(scoring_ctx, v2_payload)
            
            # Should return list of dicts with label, value, tone, key
            if not isinstance(result, list):
                self.log("Expected list", "FAIL")
                return False
            
            if len(result) < 5:
                self.log(f"Expected at least 5 rows, got {len(result)}", "FAIL")
                return False
            
            # Check structure of first row
            first = result[0]
            required_keys = ["label", "value", "tone", "key"]
            for key in required_keys:
                if key not in first:
                    self.log(f"Missing key in row: {key}", "FAIL")
                    return False
            
            self.log(f"Generated {len(result)} checklist rows", "PASS")
            for row in result:
                self.log(f"  {row['label']}: {row['value']} ({row['tone']})", "INFO")
            
            return True
        except Exception as e:
            self.log(f"Test failed: {e}", "FAIL")
            return False

    def test_build_confidence_breakdown(self):
        """Test build_confidence_breakdown with displayed_total matching"""
        self.log("Testing build_confidence_breakdown...", "INFO")
        try:
            sys.path.insert(0, '/app/backend')
            from services.mlb_pregame_analytics_v3 import build_confidence_breakdown
            
            scoring_ctx = {
                "home_pitcher_quality": {"score": 70},
                "away_pitcher_quality": {"score": 68},
                "offense_home": {"score": 55},
                "offense_away": {"score": 52},
                "bullpen": {"score": 60},
                "park": {"park_runs_mult": 1.0, "weather_score": 50},
            }
            v2_payload = {}
            displayed_total = 68.0
            
            result = build_confidence_breakdown(
                scoring_ctx, v2_payload, displayed_total=displayed_total
            )
            
            # Verify structure
            if "total" not in result or "components" not in result:
                self.log("Missing total or components", "FAIL")
                return False
            
            # Check total matches displayed_total (within ±1)
            if abs(result["total"] - displayed_total) > 1.0:
                self.log(f"Total {result['total']} doesn't match displayed {displayed_total}", "FAIL")
                return False
            
            # Check 5 components
            components = result["components"]
            if len(components) != 5:
                self.log(f"Expected 5 components, got {len(components)}", "FAIL")
                return False
            
            expected_keys = ["pitchers", "lineups", "bullpens", "park", "historical"]
            for comp in components:
                if comp["key"] not in expected_keys:
                    self.log(f"Unexpected component key: {comp['key']}", "FAIL")
                    return False
            
            # Verify sum of components equals total (within ±1)
            comp_sum = sum(c["value"] for c in components)
            if abs(comp_sum - result["total"]) > 1.0:
                self.log(f"Component sum {comp_sum} doesn't match total {result['total']}", "FAIL")
                return False
            
            self.log(f"Total: {result['total']} (matches displayed {displayed_total})", "PASS")
            for comp in components:
                self.log(f"  {comp['label']}: {comp['value']} ({comp['weight']}%)", "INFO")
            
            return True
        except Exception as e:
            self.log(f"Test failed: {e}", "FAIL")
            return False

    def test_generate_baseball_first_reasons(self):
        """Test generate_baseball_first_reasons produces baseball-specific text"""
        self.log("Testing generate_baseball_first_reasons...", "INFO")
        try:
            sys.path.insert(0, '/app/backend')
            from services.mlb_pregame_analytics_v3 import generate_baseball_first_reasons
            
            scoring_ctx = {
                "home_pitcher_quality": {"score": 72},
                "away_pitcher_quality": {"score": 68},
                "offense_home": {"score": 55},
                "offense_away": {"score": 50},
                "park": {"park_runs_mult": 0.97, "weather_score": 40},
                "home_pitcher_stats": {"name": "Cole", "throws_hand": "R"},
                "away_pitcher_stats": {"name": "Bieber", "throws_hand": "R"},
            }
            v2_payload = {
                "expectedRuns": 7.8,
                "recommendedLine": "Under 9.5",
            }
            chosen_market = {"market": "Total Runs Under"}
            
            result = generate_baseball_first_reasons(
                scoring_ctx, v2_payload, chosen_market
            )
            
            # Should return list of strings
            if not isinstance(result, list):
                self.log("Expected list", "FAIL")
                return False
            
            if len(result) < 1:
                self.log("Expected at least 1 reason", "FAIL")
                return False
            
            # Check for baseball-specific terms (not generic engine phrases)
            generic_phrases = [
                "Lectura estructural detectada",
                "Mercado rescatado",
                "Línea óptima seleccionada",
            ]
            
            all_text = " ".join(result).lower()
            for phrase in generic_phrases:
                if phrase.lower() in all_text:
                    self.log(f"Found generic phrase: {phrase}", "FAIL")
                    return False
            
            # Check for baseball-specific terms
            baseball_terms = [
                "abridor", "pitcher", "carreras", "parque", "bullpen", 
                "lineup", "ops", "under", "over", "run line", "calidad"
            ]
            found_terms = [term for term in baseball_terms if term in all_text]
            
            if len(found_terms) < 2:
                self.log(f"Not enough baseball-specific terms found: {found_terms}", "FAIL")
                return False
            
            self.log(f"Generated {len(result)} baseball-first reasons", "PASS")
            for i, reason in enumerate(result, 1):
                self.log(f"  {i}. {reason}", "INFO")
            self.log(f"Baseball terms found: {found_terms}", "PASS")
            
            return True
        except Exception as e:
            self.log(f"Test failed: {e}", "FAIL")
            return False

    def test_apply_market_diversification(self):
        """Test apply_market_diversification with dominant market"""
        self.log("Testing apply_market_diversification...", "INFO")
        try:
            sys.path.insert(0, '/app/backend')
            from services.mlb_pregame_analytics_v3 import apply_market_diversification
            
            # Create 4 picks where 3 are Under 9.5 (75% share)
            picks = [
                {
                    "recommendation": {"market": "Under 9.5"},
                    "_mlb_script_v2": {"recommendedLine": "Under 9.5"},
                },
                {
                    "recommendation": {"market": "Under 9.5"},
                    "_mlb_script_v2": {"recommendedLine": "Under 9.5"},
                },
                {
                    "recommendation": {"market": "Under 9.5"},
                    "_mlb_script_v2": {"recommendedLine": "Under 9.5"},
                },
                {
                    "recommendation": {"market": "Run Line +1.5"},
                    "_mlb_script_v2": {"recommendedLine": "Run Line +1.5"},
                },
            ]
            
            result = apply_market_diversification(picks)
            
            # Should annotate all picks with _mlb_script_v3_diversity
            for pick in result:
                if "_mlb_script_v3_diversity" not in pick:
                    self.log("Missing _mlb_script_v3_diversity", "FAIL")
                    return False
            
            # Check first pick (Under 9.5 - should be dominant)
            div = result[0]["_mlb_script_v3_diversity"]
            if not div.get("is_dominant"):
                self.log("Expected is_dominant=True for Under picks", "FAIL")
                return False
            
            if div.get("dominant_share") < 0.60:
                self.log(f"Expected dominant_share >= 0.60, got {div.get('dominant_share')}", "FAIL")
                return False
            
            if div.get("diversity_penalty") < 6:
                self.log(f"Expected diversity_penalty >= 6, got {div.get('diversity_penalty')}", "FAIL")
                return False
            
            # Check last pick (Run Line - should not be dominant)
            div_last = result[3]["_mlb_script_v3_diversity"]
            if div_last.get("is_dominant"):
                self.log("Expected is_dominant=False for Run Line pick", "FAIL")
                return False
            
            self.log(f"Dominant market: {div['dominant_market']}", "PASS")
            self.log(f"Dominant share: {div['dominant_share']}", "INFO")
            self.log(f"Diversity penalty: {div['diversity_penalty']}", "INFO")
            
            return True
        except Exception as e:
            self.log(f"Test failed: {e}", "FAIL")
            return False

    def test_apply_market_diversification_empty(self):
        """Test apply_market_diversification with empty/missing data"""
        self.log("Testing apply_market_diversification (empty)...", "INFO")
        try:
            sys.path.insert(0, '/app/backend')
            from services.mlb_pregame_analytics_v3 import apply_market_diversification
            
            # Empty list
            result = apply_market_diversification([])
            if result != []:
                self.log("Expected empty list", "FAIL")
                return False
            
            # Less than 3 picks (no dominance)
            picks = [
                {"recommendation": {"market": "Under 9.5"}, "_mlb_script_v2": {}},
                {"recommendation": {"market": "Under 9.5"}, "_mlb_script_v2": {}},
            ]
            result = apply_market_diversification(picks)
            
            for pick in result:
                div = pick.get("_mlb_script_v3_diversity", {})
                if div.get("is_dominant"):
                    self.log("Expected is_dominant=False with <3 picks", "FAIL")
                    return False
            
            self.log("Graceful handling of empty/small inputs", "PASS")
            return True
        except Exception as e:
            self.log(f"Test failed: {e}", "FAIL")
            return False

    # ========================================================================
    # 2. AUTHENTICATION
    # ========================================================================
    def test_auth_login(self):
        """Test authentication with demo credentials"""
        self.log("Testing authentication...", "INFO")
        try:
            response = requests.post(
                f"{BASE_URL}/api/auth/login",
                json={"email": "demo@valuebet.app", "password": "demo1234"},
                timeout=10
            )
            
            if response.status_code != 200:
                self.log(f"Login failed: {response.status_code}", "FAIL")
                return False
            
            data = response.json()
            if "token" not in data:
                self.log("No token in response", "FAIL")
                return False
            
            self.token = data["token"]
            self.log(f"Login successful, token: {self.token[:20]}...", "PASS")
            return True
        except Exception as e:
            self.log(f"Auth failed: {e}", "FAIL")
            return False

    # ========================================================================
    # 3. FULL PIPELINE INTEGRATION TESTS
    # ========================================================================
    def test_mlb_pipeline_v3_structure(self):
        """Test POST /api/analysis/run with sport=baseball returns _mlb_script_v3"""
        self.log("Testing MLB pipeline V3 structure...", "INFO")
        try:
            if not self.token:
                self.log("No auth token, skipping", "FAIL")
                return False
            
            response = requests.post(
                f"{BASE_URL}/api/analysis/run",
                json={"sport": "baseball"},
                headers={"Authorization": f"Bearer {self.token}"},
                timeout=60
            )
            
            if response.status_code != 200:
                self.log(f"Request failed: {response.status_code}", "FAIL")
                return False
            
            data = response.json()
            
            # Check for picks
            picks = data.get("picks", [])
            rescued = data.get("rescued_picks", [])
            all_picks = picks + rescued
            
            if not all_picks:
                self.log("No picks returned (may be valid if no games today)", "WARN")
                # Check if there are structural_lean or watchlist picks
                structural = data.get("structural_lean_requires_odds", [])
                watchlist = data.get("watchlist_manual_odds", [])
                all_picks = structural + watchlist
                
                if not all_picks:
                    self.log("No picks in any bucket", "WARN")
                    return True  # Not a failure, just no games
            
            # Check first pick for _mlb_script_v3
            pick = all_picks[0]
            
            if "_mlb_script_v3" not in pick:
                self.log("Missing _mlb_script_v3 in pick", "FAIL")
                return False
            
            v3 = pick["_mlb_script_v3"]
            
            # Check required fields
            required_fields = [
                "script", "pitchers_block", "why_this_pick", 
                "confidence_breakdown", "baseball_reasons"
            ]
            for field in required_fields:
                if field not in v3:
                    self.log(f"Missing field in _mlb_script_v3: {field}", "FAIL")
                    return False
            
            self.log(f"Found _mlb_script_v3 in pick", "PASS")
            self.log(f"Script code: {v3['script'].get('script_code')}", "INFO")
            self.log(f"Baseball reasons count: {len(v3.get('baseball_reasons', []))}", "INFO")
            
            return True
        except Exception as e:
            self.log(f"Test failed: {e}", "FAIL")
            return False

    def test_mlb_v3_script_code_valid(self):
        """Test that script_code is one of the 8 valid codes"""
        self.log("Testing MLB V3 script_code validity...", "INFO")
        try:
            if not self.token:
                self.log("No auth token, skipping", "FAIL")
                return False
            
            response = requests.post(
                f"{BASE_URL}/api/analysis/run",
                json={"sport": "baseball"},
                headers={"Authorization": f"Bearer {self.token}"},
                timeout=60
            )
            
            if response.status_code != 200:
                self.log(f"Request failed: {response.status_code}", "FAIL")
                return False
            
            data = response.json()
            picks = data.get("picks", []) + data.get("rescued_picks", [])
            
            if not picks:
                self.log("No picks to test", "WARN")
                return True
            
            valid_codes = [
                "LOW_SCORING_PITCHERS_DUEL",
                "OFFENSIVE_SHOOTOUT",
                "FAVORITE_DOMINANCE",
                "BULLPEN_BATTLE",
                "UNDERDOG_CAN_COMPETE",
                "PITCHER_MISMATCH",
                "HIGH_VARIANCE_GAME",
                "LOW_VARIANCE_GAME",
            ]
            
            for pick in picks:
                v3 = pick.get("_mlb_script_v3", {})
                script = v3.get("script", {})
                code = script.get("script_code")
                
                if code not in valid_codes:
                    self.log(f"Invalid script_code: {code}", "FAIL")
                    return False
                
                self.log(f"Valid script_code: {code}", "PASS")
            
            return True
        except Exception as e:
            self.log(f"Test failed: {e}", "FAIL")
            return False

    def test_mlb_v3_pitcher_block_structure(self):
        """Test pitchers_block has home/away with qualityScore and primary_stats"""
        self.log("Testing MLB V3 pitchers_block structure...", "INFO")
        try:
            if not self.token:
                self.log("No auth token, skipping", "FAIL")
                return False
            
            response = requests.post(
                f"{BASE_URL}/api/analysis/run",
                json={"sport": "baseball"},
                headers={"Authorization": f"Bearer {self.token}"},
                timeout=60
            )
            
            if response.status_code != 200:
                self.log(f"Request failed: {response.status_code}", "FAIL")
                return False
            
            data = response.json()
            picks = data.get("picks", []) + data.get("rescued_picks", [])
            
            if not picks:
                self.log("No picks to test", "WARN")
                return True
            
            pick = picks[0]
            v3 = pick.get("_mlb_script_v3", {})
            pitchers = v3.get("pitchers_block", {})
            
            # Check home pitcher
            home = pitchers.get("home", {})
            if not home.get("name"):
                self.log("Missing home pitcher name", "FAIL")
                return False
            if "qualityScore" not in home:
                self.log("Missing home qualityScore", "FAIL")
                return False
            if not isinstance(home.get("primary_stats"), list):
                self.log("Missing or invalid home primary_stats", "FAIL")
                return False
            if len(home["primary_stats"]) < 1 or len(home["primary_stats"]) > 2:
                self.log(f"Expected 1-2 primary_stats, got {len(home['primary_stats'])}", "FAIL")
                return False
            
            # Check away pitcher
            away = pitchers.get("away", {})
            if not away.get("name"):
                self.log("Missing away pitcher name", "FAIL")
                return False
            if "qualityScore" not in away:
                self.log("Missing away qualityScore", "FAIL")
                return False
            
            self.log(f"Home pitcher: {home['name']} (Q={home['qualityScore']})", "PASS")
            self.log(f"Home stats: {home['primary_stats']}", "INFO")
            self.log(f"Away pitcher: {away['name']} (Q={away['qualityScore']})", "PASS")
            
            return True
        except Exception as e:
            self.log(f"Test failed: {e}", "FAIL")
            return False

    def test_mlb_v3_pitcher_quality_matches_scoring_ctx(self):
        """Test pitchers_block qualityScore matches scoring_ctx"""
        self.log("Testing MLB V3 pitcher quality score matching...", "INFO")
        try:
            if not self.token:
                self.log("No auth token, skipping", "FAIL")
                return False
            
            response = requests.post(
                f"{BASE_URL}/api/analysis/run",
                json={"sport": "baseball"},
                headers={"Authorization": f"Bearer {self.token}"},
                timeout=60
            )
            
            if response.status_code != 200:
                self.log(f"Request failed: {response.status_code}", "FAIL")
                return False
            
            data = response.json()
            picks = data.get("picks", []) + data.get("rescued_picks", [])
            
            if not picks:
                self.log("No picks to test", "WARN")
                return True
            
            pick = picks[0]
            v3 = pick.get("_mlb_script_v3", {})
            pitchers = v3.get("pitchers_block", {})
            
            # Get scoring_ctx from all_components
            all_comp = pick.get("all_components", {})
            home_q_ctx = all_comp.get("home_pitcher_quality", {}).get("score")
            away_q_ctx = all_comp.get("away_pitcher_quality", {}).get("score")
            
            home_q_v3 = pitchers.get("home", {}).get("qualityScore")
            away_q_v3 = pitchers.get("away", {}).get("qualityScore")
            
            if home_q_ctx is not None and home_q_v3 is not None:
                if abs(home_q_ctx - home_q_v3) > 1:
                    self.log(f"Home quality mismatch: ctx={home_q_ctx} v3={home_q_v3}", "FAIL")
                    return False
            
            if away_q_ctx is not None and away_q_v3 is not None:
                if abs(away_q_ctx - away_q_v3) > 1:
                    self.log(f"Away quality mismatch: ctx={away_q_ctx} v3={away_q_v3}", "FAIL")
                    return False
            
            self.log(f"Pitcher quality scores match", "PASS")
            return True
        except Exception as e:
            self.log(f"Test failed: {e}", "FAIL")
            return False

    def test_mlb_v3_why_this_pick_rows(self):
        """Test why_this_pick has at least 5 rows"""
        self.log("Testing MLB V3 why_this_pick rows...", "INFO")
        try:
            if not self.token:
                self.log("No auth token, skipping", "FAIL")
                return False
            
            response = requests.post(
                f"{BASE_URL}/api/analysis/run",
                json={"sport": "baseball"},
                headers={"Authorization": f"Bearer {self.token}"},
                timeout=60
            )
            
            if response.status_code != 200:
                self.log(f"Request failed: {response.status_code}", "FAIL")
                return False
            
            data = response.json()
            picks = data.get("picks", []) + data.get("rescued_picks", [])
            
            if not picks:
                self.log("No picks to test", "WARN")
                return True
            
            pick = picks[0]
            v3 = pick.get("_mlb_script_v3", {})
            why = v3.get("why_this_pick", [])
            
            if len(why) < 5:
                self.log(f"Expected at least 5 rows, got {len(why)}", "FAIL")
                return False
            
            self.log(f"Found {len(why)} why_this_pick rows", "PASS")
            for row in why[:3]:
                self.log(f"  {row.get('label')}: {row.get('value')}", "INFO")
            
            return True
        except Exception as e:
            self.log(f"Test failed: {e}", "FAIL")
            return False

    def test_mlb_v3_confidence_breakdown_components(self):
        """Test confidence_breakdown has 5 components"""
        self.log("Testing MLB V3 confidence_breakdown components...", "INFO")
        try:
            if not self.token:
                self.log("No auth token, skipping", "FAIL")
                return False
            
            response = requests.post(
                f"{BASE_URL}/api/analysis/run",
                json={"sport": "baseball"},
                headers={"Authorization": f"Bearer {self.token}"},
                timeout=60
            )
            
            if response.status_code != 200:
                self.log(f"Request failed: {response.status_code}", "FAIL")
                return False
            
            data = response.json()
            picks = data.get("picks", []) + data.get("rescued_picks", [])
            
            if not picks:
                self.log("No picks to test", "WARN")
                return True
            
            pick = picks[0]
            v3 = pick.get("_mlb_script_v3", {})
            breakdown = v3.get("confidence_breakdown", {})
            components = breakdown.get("components", [])
            
            if len(components) != 5:
                self.log(f"Expected 5 components, got {len(components)}", "FAIL")
                return False
            
            expected_keys = ["pitchers", "lineups", "bullpens", "park", "historical"]
            for comp in components:
                if comp.get("key") not in expected_keys:
                    self.log(f"Unexpected component key: {comp.get('key')}", "FAIL")
                    return False
            
            self.log(f"Found 5 confidence components", "PASS")
            for comp in components:
                self.log(f"  {comp.get('label')}: {comp.get('value')}", "INFO")
            
            return True
        except Exception as e:
            self.log(f"Test failed: {e}", "FAIL")
            return False

    def test_mlb_v3_confidence_total_matches_score(self):
        """Test confidence_breakdown.total matches recommendation.score"""
        self.log("Testing MLB V3 confidence total matching...", "INFO")
        try:
            if not self.token:
                self.log("No auth token, skipping", "FAIL")
                return False
            
            response = requests.post(
                f"{BASE_URL}/api/analysis/run",
                json={"sport": "baseball"},
                headers={"Authorization": f"Bearer {self.token}"},
                timeout=60
            )
            
            if response.status_code != 200:
                self.log(f"Request failed: {response.status_code}", "FAIL")
                return False
            
            data = response.json()
            picks = data.get("picks", []) + data.get("rescued_picks", [])
            
            if not picks:
                self.log("No picks to test", "WARN")
                return True
            
            pick = picks[0]
            v3 = pick.get("_mlb_script_v3", {})
            breakdown = v3.get("confidence_breakdown", {})
            total = breakdown.get("total")
            
            rec = pick.get("recommendation", {})
            score = rec.get("score")
            
            if total is None or score is None:
                self.log("Missing total or score", "WARN")
                return True
            
            if abs(total - score) > 1:
                self.log(f"Total {total} doesn't match score {score}", "FAIL")
                return False
            
            self.log(f"Confidence total {total} matches score {score}", "PASS")
            return True
        except Exception as e:
            self.log(f"Test failed: {e}", "FAIL")
            return False

    def test_mlb_v3_baseball_reasons_no_generic(self):
        """Test baseball_reasons don't contain generic engine phrases"""
        self.log("Testing MLB V3 baseball_reasons (no generic phrases)...", "INFO")
        try:
            if not self.token:
                self.log("No auth token, skipping", "FAIL")
                return False
            
            response = requests.post(
                f"{BASE_URL}/api/analysis/run",
                json={"sport": "baseball"},
                headers={"Authorization": f"Bearer {self.token}"},
                timeout=60
            )
            
            if response.status_code != 200:
                self.log(f"Request failed: {response.status_code}", "FAIL")
                return False
            
            data = response.json()
            picks = data.get("picks", []) + data.get("rescued_picks", [])
            
            if not picks:
                self.log("No picks to test", "WARN")
                return True
            
            pick = picks[0]
            v3 = pick.get("_mlb_script_v3", {})
            reasons = v3.get("baseball_reasons", [])
            
            if len(reasons) < 1:
                self.log("Expected at least 1 baseball reason", "FAIL")
                return False
            
            # Check for generic phrases
            generic_phrases = [
                "Lectura estructural detectada",
                "Mercado rescatado",
                "Línea óptima seleccionada",
            ]
            
            all_text = " ".join(reasons)
            for phrase in generic_phrases:
                if phrase in all_text:
                    self.log(f"Found generic phrase: {phrase}", "FAIL")
                    return False
            
            # Check for baseball-specific terms
            baseball_terms = [
                "abridor", "pitcher", "carreras", "parque", "bullpen", 
                "lineup", "ops", "under", "over", "run line", "calidad",
                "ofensiva", "pitching", "margen", "proyect"
            ]
            all_text_lower = all_text.lower()
            found_terms = [term for term in baseball_terms if term in all_text_lower]
            
            if len(found_terms) < 2:
                self.log(f"Not enough baseball-specific terms: {found_terms}", "FAIL")
                return False
            
            self.log(f"Found {len(reasons)} baseball-first reasons", "PASS")
            for i, reason in enumerate(reasons, 1):
                self.log(f"  {i}. {reason[:80]}...", "INFO")
            self.log(f"Baseball terms found: {found_terms[:5]}", "PASS")
            
            return True
        except Exception as e:
            self.log(f"Test failed: {e}", "FAIL")
            return False

    def test_mlb_v3_diversity_present(self):
        """Test _mlb_script_v3_diversity is present on picks"""
        self.log("Testing MLB V3 diversity metadata...", "INFO")
        try:
            if not self.token:
                self.log("No auth token, skipping", "FAIL")
                return False
            
            response = requests.post(
                f"{BASE_URL}/api/analysis/run",
                json={"sport": "baseball"},
                headers={"Authorization": f"Bearer {self.token}"},
                timeout=60
            )
            
            if response.status_code != 200:
                self.log(f"Request failed: {response.status_code}", "FAIL")
                return False
            
            data = response.json()
            picks = data.get("picks", []) + data.get("rescued_picks", [])
            
            if not picks:
                self.log("No picks to test", "WARN")
                return True
            
            for pick in picks:
                if "_mlb_script_v3_diversity" not in pick:
                    self.log("Missing _mlb_script_v3_diversity", "FAIL")
                    return False
                
                div = pick["_mlb_script_v3_diversity"]
                required_keys = ["dominant_market", "dominant_share", "is_dominant", 
                               "alt_suggestions", "diversity_penalty"]
                for key in required_keys:
                    if key not in div:
                        self.log(f"Missing diversity key: {key}", "FAIL")
                        return False
            
            # Check if any pick is dominant
            dominant_picks = [p for p in picks if p["_mlb_script_v3_diversity"]["is_dominant"]]
            if dominant_picks:
                div = dominant_picks[0]["_mlb_script_v3_diversity"]
                self.log(f"Found dominant market: {div['dominant_market']}", "PASS")
                self.log(f"Dominant share: {div['dominant_share']}", "INFO")
                self.log(f"Diversity penalty: {div['diversity_penalty']}", "INFO")
            else:
                self.log("No dominant market detected (healthy diversification)", "PASS")
            
            return True
        except Exception as e:
            self.log(f"Test failed: {e}", "FAIL")
            return False

    def test_mlb_v3_diversity_penalty_when_dominant(self):
        """Test diversity_penalty >= 6 when is_dominant=true and share >= 60%"""
        self.log("Testing MLB V3 diversity penalty logic...", "INFO")
        try:
            if not self.token:
                self.log("No auth token, skipping", "FAIL")
                return False
            
            response = requests.post(
                f"{BASE_URL}/api/analysis/run",
                json={"sport": "baseball"},
                headers={"Authorization": f"Bearer {self.token}"},
                timeout=60
            )
            
            if response.status_code != 200:
                self.log(f"Request failed: {response.status_code}", "FAIL")
                return False
            
            data = response.json()
            picks = data.get("picks", []) + data.get("rescued_picks", [])
            
            if not picks:
                self.log("No picks to test", "WARN")
                return True
            
            if len(picks) < 3:
                self.log("Less than 3 picks, dominance not applicable", "WARN")
                return True
            
            # Check for dominant picks
            for pick in picks:
                div = pick.get("_mlb_script_v3_diversity", {})
                if div.get("is_dominant") and div.get("dominant_share", 0) >= 0.60:
                    penalty = div.get("diversity_penalty", 0)
                    if penalty < 6:
                        self.log(f"Expected penalty >= 6, got {penalty}", "FAIL")
                        return False
                    self.log(f"Dominant pick has penalty {penalty}", "PASS")
            
            return True
        except Exception as e:
            self.log(f"Test failed: {e}", "FAIL")
            return False

    # ========================================================================
    # 4. REGRESSION TESTS (Football/Basketball)
    # ========================================================================
    def test_football_no_mlb_v3_fields(self):
        """Test football pipeline doesn't have _mlb_script_v3 or baseball_reasons"""
        self.log("Testing football pipeline (no MLB V3 regression)...", "INFO")
        try:
            if not self.token:
                self.log("No auth token, skipping", "FAIL")
                return False
            
            response = requests.post(
                f"{BASE_URL}/api/analysis/run",
                json={"sport": "football"},
                headers={"Authorization": f"Bearer {self.token}"},
                timeout=60
            )
            
            if response.status_code != 200:
                self.log(f"Request failed: {response.status_code}", "FAIL")
                return False
            
            data = response.json()
            picks = data.get("picks", [])
            
            if not picks:
                self.log("No football picks (may be valid)", "WARN")
                return True
            
            pick = picks[0]
            
            if "_mlb_script_v3" in pick:
                self.log("Found _mlb_script_v3 in football pick (REGRESSION)", "FAIL")
                return False
            
            if "baseball_reasons" in pick:
                self.log("Found baseball_reasons in football pick (REGRESSION)", "FAIL")
                return False
            
            self.log("Football pipeline clean (no MLB V3 fields)", "PASS")
            return True
        except Exception as e:
            self.log(f"Test failed: {e}", "FAIL")
            return False

    def test_basketball_no_mlb_v3_fields(self):
        """Test basketball pipeline doesn't have _mlb_script_v3 or baseball_reasons"""
        self.log("Testing basketball pipeline (no MLB V3 regression)...", "INFO")
        try:
            if not self.token:
                self.log("No auth token, skipping", "FAIL")
                return False
            
            response = requests.post(
                f"{BASE_URL}/api/analysis/run",
                json={"sport": "basketball"},
                headers={"Authorization": f"Bearer {self.token}"},
                timeout=60
            )
            
            if response.status_code != 200:
                self.log(f"Request failed: {response.status_code}", "FAIL")
                return False
            
            data = response.json()
            picks = data.get("picks", [])
            
            if not picks:
                self.log("No basketball picks (may be valid)", "WARN")
                return True
            
            pick = picks[0]
            
            if "_mlb_script_v3" in pick:
                self.log("Found _mlb_script_v3 in basketball pick (REGRESSION)", "FAIL")
                return False
            
            if "baseball_reasons" in pick:
                self.log("Found baseball_reasons in basketball pick (REGRESSION)", "FAIL")
                return False
            
            self.log("Basketball pipeline clean (no MLB V3 fields)", "PASS")
            return True
        except Exception as e:
            self.log(f"Test failed: {e}", "FAIL")
            return False

    # ========================================================================
    # 5. MLB BUCKETS PRESERVATION
    # ========================================================================
    def test_mlb_buckets_preserved(self):
        """Test MLB pipeline returns all expected buckets"""
        self.log("Testing MLB buckets preservation...", "INFO")
        try:
            if not self.token:
                self.log("No auth token, skipping", "FAIL")
                return False
            
            response = requests.post(
                f"{BASE_URL}/api/analysis/run",
                json={"sport": "baseball"},
                headers={"Authorization": f"Bearer {self.token}"},
                timeout=60
            )
            
            if response.status_code != 200:
                self.log(f"Request failed: {response.status_code}", "FAIL")
                return False
            
            data = response.json()
            
            expected_buckets = [
                "picks",
                "rescued_picks",
                "structural_lean_requires_odds",
                "watchlist_manual_odds",
                "discarded_after_full_analysis",
            ]
            
            for bucket in expected_buckets:
                if bucket not in data:
                    self.log(f"Missing bucket: {bucket}", "FAIL")
                    return False
                self.log(f"Found bucket: {bucket} ({len(data[bucket])} items)", "PASS")
            
            return True
        except Exception as e:
            self.log(f"Test failed: {e}", "FAIL")
            return False


def main():
    tester = MLBv7Tester()
    
    print(f"\n{Colors.BLUE}{'='*60}{Colors.END}")
    print(f"{Colors.BLUE}MLB ENGINE V3 (V7) — BACKEND TEST SUITE{Colors.END}")
    print(f"{Colors.BLUE}{'='*60}{Colors.END}\n")
    
    # 1. Pure function unit tests
    tester.test("Import sanity", tester.test_pure_functions_import)
    tester.test("generate_mlb_game_script (pitcher duel)", tester.test_generate_mlb_game_script_pitcher_duel)
    tester.test("generate_mlb_game_script (shootout)", tester.test_generate_mlb_game_script_shootout)
    tester.test("build_pitcher_block", tester.test_build_pitcher_block)
    tester.test("build_why_this_pick", tester.test_build_why_this_pick)
    tester.test("build_confidence_breakdown", tester.test_build_confidence_breakdown)
    tester.test("generate_baseball_first_reasons", tester.test_generate_baseball_first_reasons)
    tester.test("apply_market_diversification (dominant)", tester.test_apply_market_diversification)
    tester.test("apply_market_diversification (empty)", tester.test_apply_market_diversification_empty)
    
    # 2. Authentication
    tester.test("Authentication", tester.test_auth_login)
    
    # 3. Full pipeline integration
    tester.test("MLB pipeline V3 structure", tester.test_mlb_pipeline_v3_structure)
    tester.test("MLB V3 script_code valid", tester.test_mlb_v3_script_code_valid)
    tester.test("MLB V3 pitchers_block structure", tester.test_mlb_v3_pitcher_block_structure)
    tester.test("MLB V3 pitcher quality matches", tester.test_mlb_v3_pitcher_quality_matches_scoring_ctx)
    tester.test("MLB V3 why_this_pick rows", tester.test_mlb_v3_why_this_pick_rows)
    tester.test("MLB V3 confidence_breakdown components", tester.test_mlb_v3_confidence_breakdown_components)
    tester.test("MLB V3 confidence total matches score", tester.test_mlb_v3_confidence_total_matches_score)
    tester.test("MLB V3 baseball_reasons no generic", tester.test_mlb_v3_baseball_reasons_no_generic)
    tester.test("MLB V3 diversity present", tester.test_mlb_v3_diversity_present)
    tester.test("MLB V3 diversity penalty logic", tester.test_mlb_v3_diversity_penalty_when_dominant)
    
    # 4. Regression tests
    tester.test("Football no MLB V3 fields", tester.test_football_no_mlb_v3_fields)
    tester.test("Basketball no MLB V3 fields", tester.test_basketball_no_mlb_v3_fields)
    
    # 5. MLB buckets preservation
    tester.test("MLB buckets preserved", tester.test_mlb_buckets_preserved)
    
    # Summary
    success = tester.summary()
    
    # Save results
    report = {
        "test_suite": "MLB Engine V3 (V7) Backend Tests",
        "timestamp": datetime.now().isoformat(),
        "total_tests": tester.tests_run,
        "passed": tester.tests_passed,
        "failed": tester.tests_failed,
        "success_rate": f"{(tester.tests_passed/tester.tests_run*100):.1f}%",
        "results": tester.results,
    }
    
    with open("/app/test_reports/mlb_v7_backend_test.json", "w") as f:
        json.dump(report, f, indent=2)
    
    print(f"\n{Colors.BLUE}Test report saved to: /app/test_reports/mlb_v7_backend_test.json{Colors.END}\n")
    
    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
