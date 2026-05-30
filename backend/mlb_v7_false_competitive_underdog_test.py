"""
MLB Engine V7 — FALSE_COMPETITIVE_UNDERDOG_SCRIPT Backend Test Suite

Tests all requirements from the MLB-V7 review_request:
1. Pure function unit tests for mlb_false_competitive_underdog.py
2. Full pipeline integration via POST /api/analysis/run
3. Canonical Yankees @ Athletics test case (BLOCK + SWAP)
4. PENALTY-ONLY case (gap ≈17, MODERATE)
5. NEGATIVE case (non-target market)
6. NO-RISK case (gap < 15)
7. Sport-gating (football/basketball should NOT have V7 fields)
8. Regression tests (V1-V6 fields still present)
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

class MLBv7FCUTester:
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
        """Test that mlb_false_competitive_underdog exposes all required functions"""
        self.log("Testing V7 module imports...", "INFO")
        try:
            sys.path.insert(0, '/app/backend')
            from services import mlb_false_competitive_underdog as fcu
            
            required = [
                'evaluate_false_competitive_underdog',
                'evaluate_favorite_top_offense',
                'evaluate_underdog_weak_bullpen',
                'evaluate_offensive_gap',
                'build_alternative_markets_proposal',
                'apply_false_competitive_underdog_to_pick',
                'TOP_OFFENCE_OPS',
                'TOP_OFFENCE_SCORE',
                'WEAK_BULLPEN_ERA_7D',
                'WEAK_BULLPEN_ERA_15D',
                'GAP_MODERATE',
                'GAP_HIGH',
                'GAP_EXTREME',
                'PENALTY_BY_GAP',
            ]
            
            for func_name in required:
                if not hasattr(fcu, func_name):
                    self.log(f"Missing function/constant: {func_name}", "FAIL")
                    return False
                self.log(f"Found: {func_name}", "PASS")
            
            # Check threshold values
            self.log(f"TOP_OFFENCE_OPS = {fcu.TOP_OFFENCE_OPS}", "INFO")
            self.log(f"TOP_OFFENCE_SCORE = {fcu.TOP_OFFENCE_SCORE}", "INFO")
            self.log(f"WEAK_BULLPEN_ERA_7D = {fcu.WEAK_BULLPEN_ERA_7D}", "INFO")
            self.log(f"GAP_MODERATE = {fcu.GAP_MODERATE}", "INFO")
            self.log(f"GAP_HIGH = {fcu.GAP_HIGH}", "INFO")
            self.log(f"GAP_EXTREME = {fcu.GAP_EXTREME}", "INFO")
            self.log(f"PENALTY_BY_GAP = {fcu.PENALTY_BY_GAP}", "INFO")
            
            return True
        except Exception as e:
            self.log(f"Import failed: {e}", "FAIL")
            return False

    def test_evaluate_favorite_top_offense(self):
        """Test evaluate_favorite_top_offense detects top offense correctly"""
        self.log("Testing evaluate_favorite_top_offense...", "INFO")
        try:
            sys.path.insert(0, '/app/backend')
            from services.mlb_false_competitive_underdog import evaluate_favorite_top_offense
            
            # Yankees case: OPS 0.795, score 78, wRC+ 118
            scoring_ctx = {
                "favorite_side": "away",
                "offense_away": {
                    "score": 78,
                    "team_ops": 0.795,
                    "wrc_plus": 118,
                    "runs_per_game_rank": 4,
                },
                "offense_home": {
                    "score": 48,
                    "team_ops": 0.700,
                },
            }
            
            result = evaluate_favorite_top_offense(scoring_ctx, favorite_side="away")
            
            # Should detect top offense
            if not result.get("is_top_offense"):
                self.log("Expected is_top_offense=True", "FAIL")
                return False
            
            if result.get("ops") != 0.795:
                self.log(f"Expected OPS 0.795, got {result.get('ops')}", "FAIL")
                return False
            
            if result.get("offensive_score") != 78:
                self.log(f"Expected score 78, got {result.get('offensive_score')}", "FAIL")
                return False
            
            if result.get("wrc_plus") != 118:
                self.log(f"Expected wRC+ 118, got {result.get('wrc_plus')}", "FAIL")
                return False
            
            confirms = result.get("confirms", [])
            if len(confirms) < 2:
                self.log(f"Expected at least 2 confirms, got {len(confirms)}", "FAIL")
                return False
            
            self.log(f"Detected top offense: {result}", "PASS")
            self.log(f"Confirms: {confirms}", "INFO")
            
            return True
        except Exception as e:
            self.log(f"Test failed: {e}", "FAIL")
            return False

    def test_evaluate_underdog_weak_bullpen(self):
        """Test evaluate_underdog_weak_bullpen detects weak bullpen with severity"""
        self.log("Testing evaluate_underdog_weak_bullpen...", "INFO")
        try:
            sys.path.insert(0, '/app/backend')
            from services.mlb_false_competitive_underdog import evaluate_underdog_weak_bullpen
            
            # Athletics case: ERA 7d 5.20, ERA 15d 4.80, fatigue 65, blowup 18%
            scoring_ctx = {
                "favorite_side": "away",
                "underdog_bullpen_era_7d": 5.20,
                "underdog_bullpen_era_15d": 4.80,
                "home_bullpen": {
                    "fatigue_score": 65,
                    "blowup_rate_pct": 18.0,
                },
            }
            
            result = evaluate_underdog_weak_bullpen(scoring_ctx, favorite_side="away")
            
            # Should detect weak bullpen
            if not result.get("is_weak"):
                self.log("Expected is_weak=True", "FAIL")
                return False
            
            # Should be HIGH severity (fatigue >= 60 AND blowup >= 15%)
            if result.get("severity") != "HIGH":
                self.log(f"Expected severity HIGH, got {result.get('severity')}", "FAIL")
                return False
            
            if result.get("era_7d") != 5.20:
                self.log(f"Expected ERA 7d 5.20, got {result.get('era_7d')}", "FAIL")
                return False
            
            factors = result.get("factors", [])
            if len(factors) < 2:
                self.log(f"Expected at least 2 factors, got {len(factors)}", "FAIL")
                return False
            
            self.log(f"Detected weak bullpen: {result}", "PASS")
            self.log(f"Factors: {factors}", "INFO")
            
            return True
        except Exception as e:
            self.log(f"Test failed: {e}", "FAIL")
            return False

    def test_evaluate_offensive_gap(self):
        """Test evaluate_offensive_gap returns gap magnitude and category"""
        self.log("Testing evaluate_offensive_gap...", "INFO")
        try:
            sys.path.insert(0, '/app/backend')
            from services.mlb_false_competitive_underdog import evaluate_offensive_gap
            
            # Yankees (78) vs Athletics (48) = gap 30 (EXTREME)
            scoring_ctx = {
                "favorite_side": "away",
                "offense_away": {"score": 78},
                "offense_home": {"score": 48},
            }
            
            result = evaluate_offensive_gap(scoring_ctx, favorite_side="away")
            
            if result.get("gap") != 30.0:
                self.log(f"Expected gap 30.0, got {result.get('gap')}", "FAIL")
                return False
            
            if result.get("magnitude") != 30.0:
                self.log(f"Expected magnitude 30.0, got {result.get('magnitude')}", "FAIL")
                return False
            
            if result.get("category") != "EXTREME":
                self.log(f"Expected category EXTREME, got {result.get('category')}", "FAIL")
                return False
            
            if result.get("favorite_score") != 78.0:
                self.log(f"Expected favorite_score 78.0, got {result.get('favorite_score')}", "FAIL")
                return False
            
            if result.get("underdog_score") != 48.0:
                self.log(f"Expected underdog_score 48.0, got {result.get('underdog_score')}", "FAIL")
                return False
            
            self.log(f"Gap evaluation: {result}", "PASS")
            
            return True
        except Exception as e:
            self.log(f"Test failed: {e}", "FAIL")
            return False

    def test_evaluate_false_competitive_underdog_canonical(self):
        """Test evaluate_false_competitive_underdog with canonical Yankees @ Athletics case"""
        self.log("Testing evaluate_false_competitive_underdog (CANONICAL)...", "INFO")
        try:
            sys.path.insert(0, '/app/backend')
            from services.mlb_false_competitive_underdog import evaluate_false_competitive_underdog
            
            # Canonical Yankees @ Athletics case
            scoring_ctx = {
                "favorite_side": "away",
                "favorite_team": "Yankees",
                "offense_away": {
                    "score": 78,
                    "team_ops": 0.795,
                    "wrc_plus": 118,
                    "runs_per_game_rank": 4,
                },
                "offense_home": {
                    "score": 48,
                    "team_ops": 0.700,
                    "wrc_plus": 92,
                },
                "underdog_bullpen_era_7d": 5.20,
                "underdog_bullpen_era_15d": 4.80,
                "home_bullpen": {
                    "fatigue_score": 65,
                    "blowup_rate_pct": 18.0,
                },
                "home_pitcher_quality": {"score": 48},
                "away_pitcher_quality": {"score": 55},
            }
            
            chosen_market = {
                "market": "Run Line +1.5 (underdog)",
                "selection": "Athletics +1.5",
                "score": 68,
            }
            
            result = evaluate_false_competitive_underdog(
                scoring_ctx,
                chosen_market,
                v2_payload={"expectedRuns": 9.2},
                over_discovery=None,
            )
            
            # Verify structure
            required_keys = [
                "is_risk", "severity", "gap_category", "gap_magnitude",
                "favorite", "underdog_bullpen", "penalty", "block_required",
                "trap_signal_code", "alternative_markets", "narrative_es",
            ]
            for key in required_keys:
                if key not in result:
                    self.log(f"Missing key: {key}", "FAIL")
                    return False
            
            # Should be BLOCK case
            if not result.get("is_risk"):
                self.log("Expected is_risk=True", "FAIL")
                return False
            
            if not result.get("block_required"):
                self.log("Expected block_required=True", "FAIL")
                return False
            
            if result.get("severity") != "EXTREME":
                self.log(f"Expected severity EXTREME, got {result.get('severity')}", "FAIL")
                return False
            
            if result.get("gap_category") != "EXTREME":
                self.log(f"Expected gap_category EXTREME, got {result.get('gap_category')}", "FAIL")
                return False
            
            if result.get("gap_magnitude") < 25:
                self.log(f"Expected gap_magnitude >= 25, got {result.get('gap_magnitude')}", "FAIL")
                return False
            
            if result.get("trap_signal_code") != "FALSE_COMPETITIVE_UNDERDOG_BLOCK":
                self.log(f"Expected trap_signal_code FALSE_COMPETITIVE_UNDERDOG_BLOCK, got {result.get('trap_signal_code')}", "FAIL")
                return False
            
            # Check favorite
            fav = result.get("favorite", {})
            if not fav.get("is_top_offense"):
                self.log("Expected favorite.is_top_offense=True", "FAIL")
                return False
            
            # Check underdog bullpen
            bp = result.get("underdog_bullpen", {})
            if not bp.get("is_weak"):
                self.log("Expected underdog_bullpen.is_weak=True", "FAIL")
                return False
            
            # Check alternative markets
            alts = result.get("alternative_markets", [])
            if len(alts) < 4:
                self.log(f"Expected at least 4 alternative markets, got {len(alts)}", "FAIL")
                return False
            
            # Top alternative should have score >= 70
            top_alt = alts[0]
            if top_alt.get("score", 0) < 70:
                self.log(f"Expected top alternative score >= 70, got {top_alt.get('score')}", "FAIL")
                return False
            
            self.log(f"CANONICAL case detected correctly", "PASS")
            self.log(f"Severity: {result.get('severity')}", "INFO")
            self.log(f"Gap: {result.get('gap_magnitude')}", "INFO")
            self.log(f"Top alternative: {top_alt.get('market')} (score {top_alt.get('score')})", "INFO")
            self.log(f"Narrative: {result.get('narrative_es')[:100]}...", "INFO")
            
            return True
        except Exception as e:
            self.log(f"Test failed: {e}", "FAIL")
            return False

    def test_evaluate_false_competitive_underdog_penalty_only(self):
        """Test evaluate_false_competitive_underdog with PENALTY-ONLY case"""
        self.log("Testing evaluate_false_competitive_underdog (PENALTY-ONLY)...", "INFO")
        try:
            sys.path.insert(0, '/app/backend')
            from services.mlb_false_competitive_underdog import evaluate_false_competitive_underdog
            
            # PENALTY-ONLY: gap ≈17 (MODERATE), top_offense=True, weak_bullpen=False
            scoring_ctx = {
                "favorite_side": "home",
                "offense_home": {
                    "score": 65,
                    "team_ops": 0.780,
                },
                "offense_away": {
                    "score": 48,
                    "team_ops": 0.710,
                },
                "underdog_bullpen_era_7d": 4.20,  # Not weak
                "underdog_bullpen_era_15d": 4.10,
                "away_bullpen": {
                    "fatigue_score": 45,
                    "blowup_rate_pct": 12.0,
                },
            }
            
            chosen_market = {
                "market": "Run Line +1.5",
                "selection": "Away +1.5",
                "score": 70,
            }
            
            result = evaluate_false_competitive_underdog(
                scoring_ctx,
                chosen_market,
            )
            
            # Should be RISK but NOT BLOCK
            if not result.get("is_risk"):
                self.log("Expected is_risk=True", "FAIL")
                return False
            
            if result.get("block_required"):
                self.log("Expected block_required=False (penalty only)", "FAIL")
                return False
            
            if result.get("severity") not in ["LOW", "MODERATE"]:
                self.log(f"Expected severity LOW or MODERATE, got {result.get('severity')}", "FAIL")
                return False
            
            # Should have penalty
            penalty = result.get("penalty", 0)
            if penalty >= 0:
                self.log(f"Expected negative penalty, got {penalty}", "FAIL")
                return False
            
            self.log(f"PENALTY-ONLY case detected correctly", "PASS")
            self.log(f"Severity: {result.get('severity')}", "INFO")
            self.log(f"Penalty: {penalty}", "INFO")
            
            return True
        except Exception as e:
            self.log(f"Test failed: {e}", "FAIL")
            return False

    def test_evaluate_false_competitive_underdog_negative(self):
        """Test evaluate_false_competitive_underdog with NEGATIVE case (non-target market)"""
        self.log("Testing evaluate_false_competitive_underdog (NEGATIVE)...", "INFO")
        try:
            sys.path.insert(0, '/app/backend')
            from services.mlb_false_competitive_underdog import evaluate_false_competitive_underdog
            
            # Non-target market: Total Runs Under 8.5
            scoring_ctx = {
                "favorite_side": "away",
                "offense_away": {"score": 78, "team_ops": 0.795},
                "offense_home": {"score": 48, "team_ops": 0.700},
                "underdog_bullpen_era_7d": 5.20,
                "underdog_bullpen_era_15d": 4.80,
            }
            
            chosen_market = {
                "market": "Total Runs Under 8.5",
                "selection": "Under 8.5",
                "score": 72,
            }
            
            result = evaluate_false_competitive_underdog(
                scoring_ctx,
                chosen_market,
            )
            
            # Should NOT be risk (not a Run Line +1.5 underdog market)
            if result.get("is_risk"):
                self.log("Expected is_risk=False for non-target market", "FAIL")
                return False
            
            if result.get("block_required"):
                self.log("Expected block_required=False for non-target market", "FAIL")
                return False
            
            self.log(f"NEGATIVE case handled correctly (non-target market)", "PASS")
            
            return True
        except Exception as e:
            self.log(f"Test failed: {e}", "FAIL")
            return False

    def test_evaluate_false_competitive_underdog_no_risk(self):
        """Test evaluate_false_competitive_underdog with NO-RISK case (gap < 15)"""
        self.log("Testing evaluate_false_competitive_underdog (NO-RISK)...", "INFO")
        try:
            sys.path.insert(0, '/app/backend')
            from services.mlb_false_competitive_underdog import evaluate_false_competitive_underdog
            
            # Balanced match-up: gap < 15
            scoring_ctx = {
                "favorite_side": "home",
                "offense_home": {"score": 55, "team_ops": 0.750},
                "offense_away": {"score": 52, "team_ops": 0.745},
                "underdog_bullpen_era_7d": 4.50,
                "underdog_bullpen_era_15d": 4.30,
            }
            
            chosen_market = {
                "market": "Run Line +1.5",
                "selection": "Away +1.5",
                "score": 68,
            }
            
            result = evaluate_false_competitive_underdog(
                scoring_ctx,
                chosen_market,
            )
            
            # Should NOT be risk (gap < 15)
            if result.get("is_risk"):
                self.log("Expected is_risk=False for balanced match-up", "FAIL")
                return False
            
            if result.get("penalty") != 0:
                self.log(f"Expected penalty=0, got {result.get('penalty')}", "FAIL")
                return False
            
            if result.get("gap_category") != "NONE":
                self.log(f"Expected gap_category NONE, got {result.get('gap_category')}", "FAIL")
                return False
            
            self.log(f"NO-RISK case handled correctly (balanced match-up)", "PASS")
            
            return True
        except Exception as e:
            self.log(f"Test failed: {e}", "FAIL")
            return False

    def test_apply_false_competitive_underdog_to_pick_block(self):
        """Test apply_false_competitive_underdog_to_pick with BLOCK case"""
        self.log("Testing apply_false_competitive_underdog_to_pick (BLOCK)...", "INFO")
        try:
            sys.path.insert(0, '/app/backend')
            from services.mlb_false_competitive_underdog import apply_false_competitive_underdog_to_pick
            
            # BLOCK case
            scoring_ctx = {
                "favorite_side": "away",
                "favorite_team": "Yankees",
                "offense_away": {"score": 78, "team_ops": 0.795, "wrc_plus": 118},
                "offense_home": {"score": 48, "team_ops": 0.700},
                "underdog_bullpen_era_7d": 5.20,
                "underdog_bullpen_era_15d": 4.80,
                "home_bullpen": {"fatigue_score": 65, "blowup_rate_pct": 18.0},
            }
            
            chosen_market = {
                "market": "Run Line +1.5 (underdog)",
                "selection": "Athletics +1.5",
                "score": 68,
                "rationale": "Original rationale",
            }
            
            new_chosen, evaluation = apply_false_competitive_underdog_to_pick(
                chosen_market,
                scoring_ctx,
                v2_payload={"expectedRuns": 9.2},
            )
            
            # Should swap market
            if not new_chosen.get("false_competitive_underdog_swap"):
                self.log("Expected false_competitive_underdog_swap=True", "FAIL")
                return False
            
            # New market should be different
            if new_chosen.get("market") == chosen_market.get("market"):
                self.log("Expected market to be swapped", "FAIL")
                return False
            
            # New score should be >= 70
            if new_chosen.get("score", 0) < 70:
                self.log(f"Expected new score >= 70, got {new_chosen.get('score')}", "FAIL")
                return False
            
            # Should have previous_market
            if new_chosen.get("previous_market") != chosen_market.get("market"):
                self.log("Expected previous_market to match original", "FAIL")
                return False
            
            self.log(f"Market swapped: {chosen_market.get('market')} → {new_chosen.get('market')}", "PASS")
            self.log(f"New score: {new_chosen.get('score')}", "INFO")
            
            return True
        except Exception as e:
            self.log(f"Test failed: {e}", "FAIL")
            return False

    def test_apply_false_competitive_underdog_to_pick_penalty(self):
        """Test apply_false_competitive_underdog_to_pick with PENALTY case"""
        self.log("Testing apply_false_competitive_underdog_to_pick (PENALTY)...", "INFO")
        try:
            sys.path.insert(0, '/app/backend')
            from services.mlb_false_competitive_underdog import apply_false_competitive_underdog_to_pick
            
            # PENALTY case
            scoring_ctx = {
                "favorite_side": "home",
                "offense_home": {"score": 65, "team_ops": 0.780},
                "offense_away": {"score": 48, "team_ops": 0.710},
                "underdog_bullpen_era_7d": 4.20,
                "underdog_bullpen_era_15d": 4.10,
            }
            
            chosen_market = {
                "market": "Run Line +1.5",
                "selection": "Away +1.5",
                "score": 70,
                "rationale": "Original rationale",
            }
            
            new_chosen, evaluation = apply_false_competitive_underdog_to_pick(
                chosen_market,
                scoring_ctx,
            )
            
            # Should NOT swap market
            if new_chosen.get("false_competitive_underdog_swap"):
                self.log("Expected false_competitive_underdog_swap=False (penalty only)", "FAIL")
                return False
            
            # Market should be same
            if new_chosen.get("market") != chosen_market.get("market"):
                self.log("Expected market to remain unchanged", "FAIL")
                return False
            
            # Score should be reduced
            if new_chosen.get("score") >= chosen_market.get("score"):
                self.log(f"Expected score to be reduced, got {new_chosen.get('score')}", "FAIL")
                return False
            
            # Should have false_competitive_underdog_meta
            if "false_competitive_underdog_meta" not in new_chosen:
                self.log("Expected false_competitive_underdog_meta", "FAIL")
                return False
            
            self.log(f"Penalty applied: {chosen_market.get('score')} → {new_chosen.get('score')}", "PASS")
            
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
    def test_mlb_pipeline_v7_fields(self):
        """Test POST /api/analysis/run with sport=baseball returns V7 fields"""
        self.log("Testing MLB pipeline V7 fields...", "INFO")
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
                return True
            
            # Check first pick for V7 fields
            pick = all_picks[0]
            
            # Should have _mlb_false_competitive_underdog
            if "_mlb_false_competitive_underdog" not in pick:
                self.log("Missing _mlb_false_competitive_underdog in pick", "FAIL")
                return False
            
            v7 = pick["_mlb_false_competitive_underdog"]
            
            # Check required fields
            required_fields = [
                "is_risk", "severity", "gap_category", "gap_magnitude",
                "favorite", "underdog_bullpen", "penalty", "block_required",
                "narrative_es",
            ]
            for field in required_fields:
                if field not in v7:
                    self.log(f"Missing field in _mlb_false_competitive_underdog: {field}", "FAIL")
                    return False
            
            self.log(f"Found _mlb_false_competitive_underdog in pick", "PASS")
            self.log(f"Is risk: {v7.get('is_risk')}", "INFO")
            self.log(f"Severity: {v7.get('severity')}", "INFO")
            self.log(f"Gap category: {v7.get('gap_category')}", "INFO")
            self.log(f"Block required: {v7.get('block_required')}", "INFO")
            
            return True
        except Exception as e:
            self.log(f"Test failed: {e}", "FAIL")
            return False

    def test_mlb_v7_trap_signals(self):
        """Test that trap signals are added when applicable"""
        self.log("Testing MLB V7 trap signals...", "INFO")
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
            
            # Check for picks with V7 risk
            for pick in picks:
                v7 = pick.get("_mlb_false_competitive_underdog", {})
                if v7.get("is_risk"):
                    # Should have trap signal
                    signals = pick.get("editorial_context_signals", [])
                    trap_codes = [s.get("code") for s in signals if "FALSE_COMPETITIVE_UNDERDOG" in s.get("code", "")]
                    
                    if not trap_codes:
                        self.log("Expected trap signal for risky pick", "FAIL")
                        return False
                    
                    self.log(f"Found trap signal: {trap_codes[0]}", "PASS")
                    return True
            
            self.log("No risky picks found (may be valid)", "WARN")
            return True
        except Exception as e:
            self.log(f"Test failed: {e}", "FAIL")
            return False

    def test_mlb_v7_regression_v1_v6(self):
        """Test that V1-V6 fields are still present"""
        self.log("Testing MLB V7 regression (V1-V6 fields)...", "INFO")
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
            
            # Check for V1-V6 fields
            expected_fields = [
                "_mlb_script_v2",
                "_mlb_script_v3",
                "_mlb_script_v5",
                "_mlb_over_discovery",
            ]
            
            missing_fields = []
            for field in expected_fields:
                if field not in pick:
                    missing_fields.append(field)
            
            if missing_fields:
                self.log(f"Missing V1-V6 fields: {missing_fields}", "FAIL")
                return False
            
            self.log("All V1-V6 fields present", "PASS")
            return True
        except Exception as e:
            self.log(f"Test failed: {e}", "FAIL")
            return False

    # ========================================================================
    # 4. SPORT-GATING TESTS
    # ========================================================================
    def test_football_no_v7_fields(self):
        """Test football pipeline doesn't have V7 fields"""
        self.log("Testing football pipeline (no V7 regression)...", "INFO")
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
            
            if "_mlb_false_competitive_underdog" in pick:
                self.log("Found _mlb_false_competitive_underdog in football pick (REGRESSION)", "FAIL")
                return False
            
            if "false_competitive_underdog_severity" in pick:
                self.log("Found false_competitive_underdog_severity in football pick (REGRESSION)", "FAIL")
                return False
            
            if "false_competitive_underdog_block" in pick:
                self.log("Found false_competitive_underdog_block in football pick (REGRESSION)", "FAIL")
                return False
            
            self.log("Football pipeline clean (no V7 fields)", "PASS")
            return True
        except Exception as e:
            self.log(f"Test failed: {e}", "FAIL")
            return False

    def test_basketball_no_v7_fields(self):
        """Test basketball pipeline doesn't have V7 fields"""
        self.log("Testing basketball pipeline (no V7 regression)...", "INFO")
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
            
            if "_mlb_false_competitive_underdog" in pick:
                self.log("Found _mlb_false_competitive_underdog in basketball pick (REGRESSION)", "FAIL")
                return False
            
            self.log("Basketball pipeline clean (no V7 fields)", "PASS")
            return True
        except Exception as e:
            self.log(f"Test failed: {e}", "FAIL")
            return False


def main():
    tester = MLBv7FCUTester()
    
    print(f"\n{Colors.BLUE}{'='*60}{Colors.END}")
    print(f"{Colors.BLUE}MLB ENGINE V7 — FALSE_COMPETITIVE_UNDERDOG TEST SUITE{Colors.END}")
    print(f"{Colors.BLUE}{'='*60}{Colors.END}\n")
    
    # 1. Pure function unit tests
    tester.test("Import sanity", tester.test_pure_functions_import)
    tester.test("evaluate_favorite_top_offense", tester.test_evaluate_favorite_top_offense)
    tester.test("evaluate_underdog_weak_bullpen", tester.test_evaluate_underdog_weak_bullpen)
    tester.test("evaluate_offensive_gap", tester.test_evaluate_offensive_gap)
    tester.test("evaluate_false_competitive_underdog (CANONICAL)", tester.test_evaluate_false_competitive_underdog_canonical)
    tester.test("evaluate_false_competitive_underdog (PENALTY-ONLY)", tester.test_evaluate_false_competitive_underdog_penalty_only)
    tester.test("evaluate_false_competitive_underdog (NEGATIVE)", tester.test_evaluate_false_competitive_underdog_negative)
    tester.test("evaluate_false_competitive_underdog (NO-RISK)", tester.test_evaluate_false_competitive_underdog_no_risk)
    tester.test("apply_false_competitive_underdog_to_pick (BLOCK)", tester.test_apply_false_competitive_underdog_to_pick_block)
    tester.test("apply_false_competitive_underdog_to_pick (PENALTY)", tester.test_apply_false_competitive_underdog_to_pick_penalty)
    
    # 2. Authentication
    tester.test("Authentication", tester.test_auth_login)
    
    # 3. Full pipeline integration
    tester.test("MLB pipeline V7 fields", tester.test_mlb_pipeline_v7_fields)
    tester.test("MLB V7 trap signals", tester.test_mlb_v7_trap_signals)
    tester.test("MLB V7 regression (V1-V6)", tester.test_mlb_v7_regression_v1_v6)
    
    # 4. Sport-gating
    tester.test("Football no V7 fields", tester.test_football_no_v7_fields)
    tester.test("Basketball no V7 fields", tester.test_basketball_no_v7_fields)
    
    # Summary
    success = tester.summary()
    
    # Save results
    report = {
        "test_suite": "MLB Engine V7 FALSE_COMPETITIVE_UNDERDOG Tests",
        "timestamp": datetime.now().isoformat(),
        "total_tests": tester.tests_run,
        "passed": tester.tests_passed,
        "failed": tester.tests_failed,
        "success_rate": f"{(tester.tests_passed/tester.tests_run*100):.1f}%",
        "results": tester.results,
    }
    
    with open("/app/test_reports/mlb_v7_fcu_test.json", "w") as f:
        json.dump(report, f, indent=2)
    
    print(f"\n{Colors.BLUE}Test report saved to: /app/test_reports/mlb_v7_fcu_test.json{Colors.END}\n")
    
    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
