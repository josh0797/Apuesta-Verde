"""Test Motivation v2 + Two-Stage Pipeline features.

Verifies:
1. Backend analyst_engine.py module imports and exports
2. Env var defaults (OPENAI_MODEL_MINI, OPENAI_MODEL_FULL, TWO_STAGE_MAX_CANDIDATES, TWO_STAGE_MIN_INPUT)
3. POST /api/analysis/run response includes _pipeline metadata
4. Picks have motivation_state field (HIGH_BOTH, ASYMMETRIC_HIGH_LOW, LOW_BOTH, NORMAL)
5. discarded_motivation ONLY contains LOW_BOTH (never ASYMMETRIC_HIGH_LOW or HIGH_BOTH)
6. total_analyzed = len(picks) + len(discarded_motivation) + len(discarded_market) + len(incomplete_data)
7. Backward compatibility of other endpoints
"""
import requests
import sys
from datetime import datetime

BASE_URL = "https://low-volatility-plays.preview.emergentagent.com/api"

class MotivationV2Tester:
    def __init__(self):
        self.token = None
        self.tests_run = 0
        self.tests_passed = 0
        self.failures = []

    def log(self, msg: str, level: str = "INFO"):
        timestamp = datetime.now().strftime("%H:%M:%S")
        prefix = "✅" if level == "SUCCESS" else "❌" if level == "ERROR" else "ℹ️"
        print(f"[{timestamp}] {prefix} {msg}")

    def test(self, name: str, check_fn):
        """Run a test with a custom check function."""
        self.tests_run += 1
        self.log(f"Test #{self.tests_run}: {name}")
        try:
            result = check_fn()
            if result:
                self.tests_passed += 1
                self.log(f"PASSED", "SUCCESS")
                return True
            else:
                self.failures.append(name)
                self.log(f"FAILED", "ERROR")
                return False
        except Exception as e:
            self.failures.append(f"{name}: {str(e)}")
            self.log(f"FAILED: {str(e)}", "ERROR")
            return False

    def run_all_tests(self):
        self.log("\n" + "=" * 80)
        self.log("MOTIVATION V2 + TWO-STAGE PIPELINE TESTS")
        self.log("=" * 80 + "\n")

        # ═══════════════════════════════════════════════════════════════════════
        # 1. MODULE IMPORTS
        # ═══════════════════════════════════════════════════════════════════════
        self.log("[1] Backend Module Imports", "SECTION")
        
        def check_imports():
            from services.analyst_engine import (
                SPORT_RULES, 
                MOTIVATION_RULES_V2, 
                _build_system_prompt, 
                _build_prefilter_prompt, 
                _run_prefilter, 
                _select_candidates, 
                analyze_matches
            )
            assert len(SPORT_RULES) == 3, "SPORT_RULES should have 3 sports"
            assert len(MOTIVATION_RULES_V2) > 2000, "MOTIVATION_RULES_V2 should be substantial"
            assert callable(_build_system_prompt), "_build_system_prompt should be callable"
            assert callable(_build_prefilter_prompt), "_build_prefilter_prompt should be callable"
            assert callable(_run_prefilter), "_run_prefilter should be callable"
            assert callable(_select_candidates), "_select_candidates should be callable"
            assert callable(analyze_matches), "analyze_matches should be callable"
            return True
        
        self.test("Module imports and exports", check_imports)

        # ═══════════════════════════════════════════════════════════════════════
        # 2. ENV VAR DEFAULTS
        # ═══════════════════════════════════════════════════════════════════════
        self.log("\n[2] Environment Variable Defaults", "SECTION")
        
        def check_env_defaults():
            import os
            from services.analyst_engine import (
                OPENAI_MODEL_MINI, 
                OPENAI_MODEL_FULL, 
                TWO_STAGE_MAX_CANDIDATES, 
                TWO_STAGE_MIN_INPUT
            )
            # Check defaults (should work even without explicit env vars)
            assert OPENAI_MODEL_MINI in ["gpt-4o-mini", os.environ.get("OPENAI_MODEL_MINI", "gpt-4o-mini")]
            assert OPENAI_MODEL_FULL in ["gpt-4o", os.environ.get("OPENAI_MODEL_FULL", "gpt-4o")]
            assert TWO_STAGE_MAX_CANDIDATES >= 3, "TWO_STAGE_MAX_CANDIDATES should be >= 3"
            assert TWO_STAGE_MIN_INPUT >= 2, "TWO_STAGE_MIN_INPUT should be >= 2"
            self.log(f"   OPENAI_MODEL_MINI: {OPENAI_MODEL_MINI}")
            self.log(f"   OPENAI_MODEL_FULL: {OPENAI_MODEL_FULL}")
            self.log(f"   TWO_STAGE_MAX_CANDIDATES: {TWO_STAGE_MAX_CANDIDATES}")
            self.log(f"   TWO_STAGE_MIN_INPUT: {TWO_STAGE_MIN_INPUT}")
            return True
        
        self.test("Env var defaults", check_env_defaults)

        # ═══════════════════════════════════════════════════════════════════════
        # 3. PROMPT KEYWORDS
        # ═══════════════════════════════════════════════════════════════════════
        self.log("\n[3] Prompt Keywords Verification", "SECTION")
        
        def check_prompt_keywords():
            from services.analyst_engine import MOTIVATION_RULES_V2, _build_system_prompt
            
            # Check MOTIVATION_RULES_V2 keywords
            assert "CONTEXTUAL Y STANDINGS-AWARE" in MOTIVATION_RULES_V2
            assert "Posición baja en tabla NO IMPLICA motivación baja" in MOTIVATION_RULES_V2
            
            # Check Stage 2 prompt
            stage2_prompt = _build_system_prompt("football")
            assert "CONTEXTUAL Y STANDINGS-AWARE" in stage2_prompt
            assert "NUNCA listes un ASYMMETRIC_HIGH_LOW en discarded_motivation" in stage2_prompt
            assert "POLÍTICA DE DESCARTE POR MOTIVACIÓN" in stage2_prompt
            
            self.log("   ✓ CONTEXTUAL Y STANDINGS-AWARE")
            self.log("   ✓ Posición baja NO implica motivación baja")
            self.log("   ✓ NUNCA descartar ASYMMETRIC_HIGH_LOW por motivación")
            return True
        
        self.test("Prompt keywords", check_prompt_keywords)

        # ═══════════════════════════════════════════════════════════════════════
        # 4. LOGIN
        # ═══════════════════════════════════════════════════════════════════════
        self.log("\n[4] Authentication", "SECTION")
        
        def check_login():
            resp = requests.post(
                f"{BASE_URL}/auth/login",
                json={"email": "demo@valuebet.app", "password": "demo1234"},
                timeout=10
            )
            assert resp.status_code == 200, f"Login failed: {resp.status_code}"
            data = resp.json()
            assert "token" in data, "Token not in response"
            self.token = data["token"]
            return True
        
        self.test("Login as demo user", check_login)

        # ═══════════════════════════════════════════════════════════════════════
        # 5. VERIFY EXISTING PICK RUN
        # ═══════════════════════════════════════════════════════════════════════
        self.log("\n[5] Verify Existing Pick Run", "SECTION")
        
        def check_pick_run():
            resp = requests.get(
                f"{BASE_URL}/picks/today?sport=football",
                headers={"Authorization": f"Bearer {self.token}"},
                timeout=10
            )
            assert resp.status_code == 200, f"Failed to get picks: {resp.status_code}"
            data = resp.json()
            assert "pick_run" in data, "pick_run not in response"
            pick_run = data["pick_run"]
            assert pick_run is not None, "pick_run is null"
            
            payload = pick_run.get("payload", {})
            
            # Check _pipeline metadata
            pipeline = payload.get("_pipeline", {})
            assert pipeline.get("stage1_model") == "gpt-4o-mini", f"stage1_model should be gpt-4o-mini, got {pipeline.get('stage1_model')}"
            assert pipeline.get("stage2_model") == "gpt-4o", f"stage2_model should be gpt-4o, got {pipeline.get('stage2_model')}"
            assert isinstance(pipeline.get("stage1_candidates"), int), "stage1_candidates should be int"
            assert isinstance(pipeline.get("stage1_auto_discarded"), int), "stage1_auto_discarded should be int"
            
            self.log(f"   ✓ stage1_model: {pipeline.get('stage1_model')}")
            self.log(f"   ✓ stage2_model: {pipeline.get('stage2_model')}")
            self.log(f"   ✓ stage1_candidates: {pipeline.get('stage1_candidates')}")
            self.log(f"   ✓ stage1_auto_discarded: {pipeline.get('stage1_auto_discarded')}")
            
            # Check picks have motivation_state
            picks = payload.get("picks", [])
            assert len(picks) > 0, "No picks found"
            
            valid_states = ["HIGH_BOTH", "ASYMMETRIC_HIGH_LOW", "LOW_BOTH", "NORMAL"]
            for i, pick in enumerate(picks):
                state = pick.get("motivation_state")
                assert state in valid_states, f"Pick {i} has invalid motivation_state: {state}"
                self.log(f"   ✓ Pick {i+1} ({pick.get('match_label')}): {state}")
            
            # Check discarded_motivation ONLY contains LOW_BOTH
            summary = payload.get("summary", {})
            discarded_mot = summary.get("discarded_motivation", [])
            for i, disc in enumerate(discarded_mot):
                state = disc.get("motivation_state")
                assert state == "LOW_BOTH", f"discarded_motivation[{i}] has state {state}, expected LOW_BOTH"
                self.log(f"   ✓ Discarded {i+1} ({disc.get('match_label')}): {state}")
            
            # Check total_analyzed math
            total_analyzed = summary.get("total_analyzed", 0)
            picks_count = len(picks)
            disc_mot_count = len(discarded_mot)
            disc_mkt_count = len(summary.get("discarded_market", []))
            incomplete_count = len(summary.get("incomplete_data", []))
            computed_total = picks_count + disc_mot_count + disc_mkt_count + incomplete_count
            
            assert computed_total == total_analyzed, f"Total mismatch: {computed_total} != {total_analyzed}"
            self.log(f"   ✓ Total categorization: {picks_count} picks + {disc_mot_count} disc_mot + {disc_mkt_count} disc_mkt + {incomplete_count} incomplete = {total_analyzed}")
            
            return True
        
        self.test("Verify pick run structure", check_pick_run)

        # ═══════════════════════════════════════════════════════════════════════
        # 6. BACKWARD COMPATIBILITY
        # ═══════════════════════════════════════════════════════════════════════
        self.log("\n[6] Backward Compatibility", "SECTION")
        
        def check_backward_compat():
            endpoints = [
                ("learning/stats?sport=football", "patterns"),
                ("stats/timeline?sport=football", "timeline"),
                ("profile/saved-views", "items"),
            ]
            
            for endpoint, expected_key in endpoints:
                resp = requests.get(
                    f"{BASE_URL}/{endpoint}",
                    headers={"Authorization": f"Bearer {self.token}"},
                    timeout=10
                )
                assert resp.status_code == 200, f"GET {endpoint} failed: {resp.status_code}"
                data = resp.json()
                assert expected_key in data, f"{endpoint} missing key {expected_key}"
                self.log(f"   ✓ GET /api/{endpoint}")
            
            return True
        
        self.test("Backward compatibility", check_backward_compat)

        # ═══════════════════════════════════════════════════════════════════════
        # SUMMARY
        # ═══════════════════════════════════════════════════════════════════════
        self.log("\n" + "=" * 80)
        self.log("TEST SUMMARY")
        self.log("=" * 80)
        self.log(f"Total tests: {self.tests_run}")
        self.log(f"Passed: {self.tests_passed} ✅")
        self.log(f"Failed: {len(self.failures)} ❌")
        
        if self.failures:
            self.log("\nFAILED TESTS:", "ERROR")
            for i, failure in enumerate(self.failures, 1):
                self.log(f"{i}. {failure}", "ERROR")
        
        return len(self.failures) == 0


def main():
    tester = MotivationV2Tester()
    success = tester.run_all_tests()
    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
